"""Orchestrator: the star-topology state machine (DESIGN_PROMPT §3, §11).

Owns the Blackboard, dispatches agents through explicit phases, runs independent
retrieval concurrently, enforces a global timeout, bounds the synthesis↔critic
loop by ``max_revision_rounds``, and writes an observable ``run_log.jsonl`` (one
line per transition plus token accounting). Agents never call each other — every
edge in the graph passes through here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TextIO

from fra.agents import (
    AnalysisAgent,
    CriticAgent,
    DataRetrievalAgent,
    LiteratureAgent,
    OceanographyAgent,
    PlannerAgent,
    SynthesisAgent,
)
from fra.blackboard import Blackboard
from fra.config import Settings
from fra.connectors.base import Connector
from fra.llm import LLMClient
from fra.models import CriticNote
from fra.report import build_all_figures, write_report

logger = logging.getLogger(__name__)


class Phase(str, Enum):
    PLANNING = "planning"
    CLARIFICATION_NEEDED = "clarification_needed"
    RETRIEVAL = "retrieval"
    ANALYSIS = "analysis"
    SYNTHESIS = "synthesis"
    CRITIQUE = "critique"
    RENDER = "render"
    DONE = "done"
    FAILED = "failed"


@dataclass
class RunResult:
    run_id: str
    phase: Phase
    blackboard: Blackboard
    out_dir: Path | None = None
    clarification_questions: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        connectors: list[Connector],
        *,
        out_root: str | Path = "runs",
        progress: bool = False,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._connectors = connectors
        self._out_root = Path(out_root)
        self._progress = progress
        rt = settings.runtime

        self._planner = PlannerAgent(llm, timeout_s=rt.agent_timeout_s)
        self._data = DataRetrievalAgent(connectors, timeout_s=rt.agent_timeout_s)
        self._ocean = OceanographyAgent(connectors, timeout_s=rt.agent_timeout_s)
        self._literature = LiteratureAgent(connectors, timeout_s=rt.agent_timeout_s)
        self._analysis = AnalysisAgent(timeout_s=rt.agent_timeout_s)
        self._synthesis = SynthesisAgent(llm, timeout_s=rt.agent_timeout_s)
        self._critic = CriticAgent(llm, timeout_s=rt.agent_timeout_s)

        self._log_fp: TextIO | None = None

    async def run(self, question: str, *, run_id: str | None = None) -> RunResult:
        rid = run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        out_dir = self._out_root / rid
        out_dir.mkdir(parents=True, exist_ok=True)
        self._log_fp = (out_dir / "run_log.jsonl").open("w", encoding="utf-8")
        bb = Blackboard(run_id=rid, question=question)
        start = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._run_inner(bb, out_dir), timeout=self._settings.runtime.global_timeout_s
            )
        except asyncio.TimeoutError:
            self._emit("timeout", phase=Phase.FAILED.value, elapsed=time.monotonic() - start)
            logger.error("run %s exceeded global timeout", rid)
            return RunResult(run_id=rid, phase=Phase.FAILED, blackboard=bb, out_dir=out_dir)
        finally:
            self._emit_tokens()
            if self._log_fp is not None:
                self._log_fp.close()
                self._log_fp = None

    async def _run_inner(self, bb: Blackboard, out_dir: Path) -> RunResult:
        # --- PLANNING -------------------------------------------------------
        self._enter(Phase.PLANNING)
        bb = await self._planner.run(bb)
        if bb.plan is None or bb.plan.needs_clarification:
            self._enter(Phase.CLARIFICATION_NEEDED)
            qs = bb.plan.clarification_questions if bb.plan else ["Could not parse the question."]
            self._emit("clarification_needed", questions=qs)
            return RunResult(
                run_id=bb.run_id,
                phase=Phase.CLARIFICATION_NEEDED,
                blackboard=bb,
                out_dir=out_dir,
                clarification_questions=qs,
            )

        # --- RETRIEVAL (concurrent) -----------------------------------------
        self._enter(Phase.RETRIEVAL)
        await asyncio.gather(self._data.run(bb), self._ocean.run(bb), self._literature.run(bb))
        # (agents mutate the shared bb in place; gather is the barrier)
        self._emit(
            "retrieval_complete",
            landings=len(bb.landings),
            assessments=len(bb.assessments),
            covariates=len(bb.covariates),
            references=len(bb.references),
            gaps=len(bb.coverage_gaps),
        )

        # --- ANALYSIS (barrier) ---------------------------------------------
        self._enter(Phase.ANALYSIS)
        bb = await self._analysis.run(bb)
        self._emit("analysis_complete", results=len(bb.analyses))

        # Figures depend only on retrieved data + analysis; build once here.
        bb.figures = build_all_figures(bb, out_dir / "figures")
        self._emit("figures_built", figures=[f.id for f in bb.figures])

        # --- SYNTHESIS <-> CRITIC loop --------------------------------------
        outstanding = await self._synthesis_critic_loop(bb)

        # --- RENDER ---------------------------------------------------------
        self._enter(Phase.RENDER)
        write_report(bb, out_dir, outstanding=outstanding)
        self._enter(Phase.DONE)
        self._emit("done", out_dir=str(out_dir))
        return RunResult(run_id=bb.run_id, phase=Phase.DONE, blackboard=bb, out_dir=out_dir)

    async def _synthesis_critic_loop(self, bb: Blackboard) -> list[CriticNote]:
        max_rounds = self._settings.runtime.max_revision_rounds
        outstanding: list[CriticNote] = []
        for rnd in range(max_rounds + 1):
            self._enter(Phase.SYNTHESIS)
            bb = await self._synthesis.run(bb)
            self._enter(Phase.CRITIQUE)
            bb = await self._critic.run(bb)
            verdict = self._critic.last_verdict
            self._emit(
                "critic_round",
                round=rnd,
                verdict=verdict.verdict,
                notes=len(verdict.notes),
                blocking=verdict.blocking,
            )
            if verdict.verdict == "pass" or not verdict.blocking:
                return []
            outstanding = list(verdict.notes)
            if rnd == max_rounds:
                # Ship with the outstanding notes rather than looping forever.
                self._emit("revision_cap_reached", round=rnd, outstanding=len(outstanding))
                return outstanding
        return outstanding

    # -- run-log helpers -----------------------------------------------------

    def _enter(self, phase: Phase) -> None:
        self._emit("phase", phase=phase.value)
        if self._progress:
            print(f"  -> {phase.value}")

    def _emit(self, event: str, **fields: object) -> None:
        if self._log_fp is None:
            return
        line = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        self._log_fp.write(json.dumps(line, default=str) + "\n")
        self._log_fp.flush()

    def _emit_tokens(self) -> None:
        ledger = self._llm.ledger
        self._emit(
            "token_usage",
            calls=ledger.calls,
            input_tokens=ledger.input_tokens,
            output_tokens=ledger.output_tokens,
            per_agent=ledger.per_agent,
        )

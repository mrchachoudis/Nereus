"""Synthesis agent: compose a grounded, cited DraftReport (DESIGN_PROMPT §2, §7)."""

from __future__ import annotations

from typing import Any

from fra.agents.base import BaseAgent, GuardResult
from fra.blackboard import Blackboard
from fra.llm import LLMClient, agent_system, agent_user, load_prompt
from fra.models import DraftReport


class SynthesisAgent(BaseAgent):
    name = "synthesis"

    def __init__(self, llm: LLMClient, *, timeout_s: float = 300.0) -> None:
        super().__init__(timeout_s=timeout_s, max_retries=2)
        self._llm = llm
        self._template = load_prompt("synthesis")

    def _guard(self, bb: Blackboard) -> GuardResult:
        if bb.plan is None:
            return GuardResult(ok=False, missing=["plan"])
        return GuardResult(ok=True)

    async def _run(self, bb: Blackboard) -> Blackboard:
        assert bb.plan is not None
        context = self._build_context(bb)
        draft = self._llm.structured(
            system=agent_system(self.name, self._template),
            user=agent_user(
                "Compose the report strictly from the artifacts in the context. "
                "Honour the grounding contract: no number without a citation to a "
                "real ref_id. Prior critic notes (if any) must be addressed.",
                context,
            ),
            schema=DraftReport,
            agent=self.name,
        )
        # Attach run figures and stamp the revision from prior critic rounds.
        if not draft.figures:
            draft.figures = list(bb.figures)
        draft.revision = len(bb.critic_notes)
        draft.run_id = bb.run_id
        bb.draft = draft
        self.log.info(
            "synthesis produced draft with %d sections, %d citations",
            len(draft.sections),
            len(draft.citations),
        )
        return bb

    def _build_context(self, bb: Blackboard) -> dict[str, Any]:
        assert bb.plan is not None
        return {
            "run_id": bb.run_id,
            "question": bb.plan.question,
            "plan": bb.plan.model_dump(mode="json"),
            "analyses": [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "target": a.target,
                    "statistic": a.statistic,
                    "p_value": a.p_value,
                    "confidence_interval": a.confidence_interval,
                    "effect_size": a.effect_size,
                    "interpretation": a.interpretation,
                    "inputs": a.inputs,
                    "detail": a.detail,
                }
                for a in bb.analyses
            ],
            "landings_refs": _record_digest(bb.landings),
            "assessment_refs": _record_digest(bb.assessments),
            "covariate_refs": [
                {"id": c.id, "variable": c.variable, "unit": c.unit, "n": len(c.values)}
                for c in bb.covariates
            ],
            "references": [
                {
                    "id": r.id,
                    "title": r.title,
                    "year": r.year,
                    "doi": r.doi,
                    "relevance": r.relevance_score,
                    "abstract": (r.abstract or "")[:400],
                }
                for r in bb.references
            ],
            "figures": [f.model_dump(mode="json") for f in bb.figures],
            "coverage_gaps": [g.model_dump(mode="json") for g in bb.coverage_gaps],
            "known_ids": sorted(bb.known_ids()),
        }


def _record_digest(records: list[Any]) -> list[dict[str, Any]]:
    """Compact list of {id, source_ref, year} for citation targeting."""
    return [
        {"id": r.id, "source_ref": r.source_ref, "year": r.year, "source": r.source}
        for r in records
    ]

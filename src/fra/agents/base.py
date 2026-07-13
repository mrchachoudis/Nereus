"""Agent protocol and shared machinery (DESIGN_PROMPT §6.3).

Agents talk only to the Blackboard and the orchestrator — never to each other
(star topology). The base class supplies structured logging, a per-agent timeout,
retry-with-backoff for the transient failures agents can hit, and a ``_guard``
that checks Blackboard preconditions before the agent body runs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from fra.blackboard import Blackboard
from fra.llm import LLMError


@runtime_checkable
class Agent(Protocol):
    name: str

    async def run(self, bb: Blackboard) -> Blackboard: ...


@dataclass
class GuardResult:
    """Outcome of a precondition check."""

    ok: bool
    missing: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "missing preconditions: " + ", ".join(self.missing)


class AgentError(RuntimeError):
    """Non-recoverable agent failure."""


class BaseAgent(ABC):
    """Common base for all agents."""

    name: str = "agent"

    def __init__(
        self,
        *,
        timeout_s: float = 300.0,
        max_retries: int = 2,
    ) -> None:
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.log = logging.getLogger(f"fra.agent.{self.name}")

    # -- overridable hooks ---------------------------------------------------

    def _guard(self, bb: Blackboard) -> GuardResult:
        """Validate preconditions. Default: always OK. Override to require inputs."""
        return GuardResult(ok=True)

    @abstractmethod
    async def _run(self, bb: Blackboard) -> Blackboard:
        """Agent body. Implementations mutate and return ``bb``."""

    # -- driver --------------------------------------------------------------

    async def run(self, bb: Blackboard) -> Blackboard:
        guard = self._guard(bb)
        if not guard.ok:
            # A failed guard is a coverage/precondition gap, not a crash: log it
            # and return the board unchanged so the orchestrator can proceed.
            self.log.warning("%s guard failed: %s", self.name, guard.reason)
            bb.add_gap(domain=self.name, detail=guard.reason)
            return bb

        start = time.monotonic()
        self.log.info("%s starting", self.name)
        try:
            result = await asyncio.wait_for(self._run_with_retry(bb), timeout=self.timeout_s)
        except asyncio.TimeoutError as exc:
            self.log.error("%s timed out after %.0fs", self.name, self.timeout_s)
            raise AgentError(f"{self.name} timed out") from exc
        self.log.info("%s done in %.2fs", self.name, time.monotonic() - start)
        return result

    async def _run_with_retry(self, bb: Blackboard) -> Blackboard:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._run(bb)
            except (AgentError, LLMError) as exc:
                # Deterministic failures (bad structured output, precondition
                # errors) won't be fixed by retrying — fail fast.
                raise AgentError(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 - transient; retry with backoff
                last_exc = exc
                backoff = min(2.0**attempt * 0.5, 8.0)
                self.log.warning(
                    "%s attempt %d/%d failed: %s; retrying in %.1fs",
                    self.name,
                    attempt,
                    self.max_retries,
                    exc,
                    backoff,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(backoff)
        raise AgentError(f"{self.name} failed after {self.max_retries} attempts: {last_exc}")

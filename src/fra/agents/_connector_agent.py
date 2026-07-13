"""Shared base for the retrieval-style agents (data, ocean, literature).

Runs its connectors concurrently (``asyncio.gather``), writes results to the
Blackboard with provenance, and records a coverage gap for any domain that
errors or returns nothing — a single connector failure degrades that domain, it
does not kill the run (DESIGN_PROMPT §11).
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from fra.agents.base import BaseAgent, GuardResult
from fra.blackboard import Blackboard
from fra.connectors.base import Connector, ConnectorError


class ConnectorAgent(BaseAgent):
    """Base class parameterized by a set of connectors and the domains it owns."""

    def __init__(
        self,
        connectors: list[Connector],
        *,
        domains: set[str],
        timeout_s: float = 300.0,
    ) -> None:
        super().__init__(timeout_s=timeout_s, max_retries=1)
        self._connectors = [c for c in connectors if getattr(c, "domain", None) in domains]
        self._domains = domains

    def _guard(self, bb: Blackboard) -> GuardResult:
        if bb.plan is None or bb.plan.needs_clarification:
            return GuardResult(ok=False, missing=["approved plan"])
        return GuardResult(ok=True)

    async def _run(self, bb: Blackboard) -> Blackboard:
        assert bb.plan is not None
        if not self._connectors:
            for domain in sorted(self._domains):
                bb.add_gap(domain=domain, detail="no connector enabled for this domain")
            return bb

        results = await asyncio.gather(
            *(self._safe_fetch(c, bb) for c in self._connectors),
            return_exceptions=False,
        )
        for connector, records in results:
            if records is None:
                bb.add_gap(
                    domain=connector.domain,
                    detail=f"connector {connector.name} failed",
                    connector=connector.name,
                )
                continue
            if not records:
                bb.add_gap(
                    domain=connector.domain,
                    detail=f"connector {connector.name} returned no records",
                    connector=connector.name,
                )
            bb.record(
                source=connector.name,
                url_or_query=getattr(connector, "config", None)
                and connector.config.base_url  # type: ignore[attr-defined]
                or connector.name,
                records=records,
            )
        return bb

    async def _safe_fetch(
        self, connector: Connector, bb: Blackboard
    ) -> tuple[Connector, list[BaseModel] | None]:
        assert bb.plan is not None
        try:
            records = await connector.fetch(bb.plan)
            self.log.info("%s fetched %d records", connector.name, len(records))
            return connector, list(records)
        except (ConnectorError, Exception) as exc:  # noqa: BLE001 - degrade, don't die
            self.log.warning("%s failed: %s", connector.name, exc)
            return connector, None

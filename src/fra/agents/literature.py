"""Literature agent: retrieve and rank primary literature.

Retrieval + relevance ranking is handled by the connectors (Crossref, OpenAlex).
This agent additionally de-duplicates by DOI across sources and keeps the
top-``max_refs`` by relevance so downstream synthesis has a focused set. Linking
references to the analyses they support/contradict is left to the Synthesis agent,
which has the analysis results in context.
"""

from __future__ import annotations

from fra.agents._connector_agent import ConnectorAgent
from fra.blackboard import Blackboard
from fra.connectors.base import Connector
from fra.models import Reference


class LiteratureAgent(ConnectorAgent):
    name = "literature"

    def __init__(
        self,
        connectors: list[Connector],
        *,
        timeout_s: float = 300.0,
        max_refs: int = 15,
    ) -> None:
        super().__init__(connectors, domains={"literature"}, timeout_s=timeout_s)
        self._max_refs = max_refs

    async def _run(self, bb: Blackboard) -> Blackboard:
        bb = await super()._run(bb)
        bb.references = self._dedup_and_rank(bb.references)
        return bb

    def _dedup_and_rank(self, refs: list[Reference]) -> list[Reference]:
        by_doi: dict[str, Reference] = {}
        keyless: list[Reference] = []
        for ref in refs:
            if ref.doi:
                # keep the higher-relevance duplicate
                existing = by_doi.get(ref.doi)
                if existing is None or ref.relevance_score > existing.relevance_score:
                    by_doi[ref.doi] = ref
            else:
                keyless.append(ref)
        merged = list(by_doi.values()) + keyless
        merged.sort(key=lambda r: r.relevance_score, reverse=True)
        return merged[: self._max_refs]

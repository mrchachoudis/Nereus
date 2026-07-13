"""Crossref literature connector.

Queries the Crossref REST API for works matching the plan's species and area,
coercing hits into :class:`Reference`. Crossref is keyless; we send a ``mailto``
in the query for the polite pool. Relevance is Crossref's own ``score`` field,
normalized to 0-1 within the returned batch.
"""

from __future__ import annotations

import os
from typing import Any

from fra.connectors.base import HttpConnector
from fra.models import Reference, ResearchPlan


class CrossrefConnector(HttpConnector):
    domain = "literature"

    def _query(self, plan: ResearchPlan) -> str:
        species = " ".join(t.scientific_name for t in plan.taxa)
        areas = " ".join(a.label for a in plan.areas)
        return f"{species} stock assessment fisheries {areas}".strip()

    async def fetch(self, plan: ResearchPlan) -> list[Reference]:
        mailto = os.environ.get("CROSSREF_MAILTO", "")
        params: dict[str, Any] = {
            "query": self._query(plan),
            "rows": int(self.config.options.get("rows", 20)),
            "select": "DOI,title,author,issued,abstract,score",
            "sort": "relevance",
        }
        if mailto:
            params["mailto"] = mailto
        if plan.time_range is not None:
            params["filter"] = (
                f"from-pub-date:{plan.time_range.start_year}-01-01,"
                f"until-pub-date:{plan.time_range.end_year}-12-31"
            )

        url = f"{self.config.base_url.rstrip('/')}/works"
        data = await self._get_json(url, params=params)
        items = data.get("message", {}).get("items", [])
        if not items:
            return []

        scores = [float(it.get("score", 0.0)) for it in items]
        max_score = max(scores) or 1.0
        refs: list[Reference] = []
        for i, it in enumerate(items):
            doi = it.get("DOI")
            title_list = it.get("title") or ["(untitled)"]
            refs.append(
                Reference(
                    id=f"crossref-{doi or i}",
                    doi=doi,
                    title=title_list[0],
                    year=_issued_year(it),
                    authors=_authors(it),
                    abstract=_strip_jats(it.get("abstract")),
                    relevance_score=round(float(it.get("score", 0.0)) / max_score, 4),
                    source="crossref",
                    url=f"https://doi.org/{doi}" if doi else None,
                )
            )
        return refs


def _issued_year(item: dict[str, Any]) -> int:
    parts = item.get("issued", {}).get("date-parts", [[None]])
    year = parts[0][0] if parts and parts[0] else None
    return int(year) if year else 1900


def _authors(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for a in item.get("author", []) or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x)
        if name:
            out.append(name)
    return out


def _strip_jats(abstract: str | None) -> str | None:
    """Crossref abstracts are JATS-XML; strip tags for a plain-text preview."""
    if not abstract:
        return None
    import re

    text = re.sub(r"<[^>]+>", " ", abstract)
    return re.sub(r"\s+", " ", text).strip() or None

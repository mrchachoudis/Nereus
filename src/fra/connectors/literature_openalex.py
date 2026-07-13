"""OpenAlex literature connector.

Queries the OpenAlex ``works`` endpoint. OpenAlex is keyless; a ``mailto`` gets
the polite pool. Abstracts arrive as an inverted index, which we reconstruct.
Relevance uses OpenAlex ``relevance_score`` when present, else a citation-based
fallback normalized to 0-1.
"""

from __future__ import annotations

import math
import os
from typing import Any

from fra.connectors.base import HttpConnector
from fra.models import Reference, ResearchPlan


class OpenAlexConnector(HttpConnector):
    domain = "literature"

    def _search(self, plan: ResearchPlan) -> str:
        species = " ".join(t.scientific_name for t in plan.taxa)
        areas = " ".join(a.label for a in plan.areas)
        return f"{species} fisheries stock {areas}".strip()

    async def fetch(self, plan: ResearchPlan) -> list[Reference]:
        params: dict[str, Any] = {
            "search": self._search(plan),
            "per-page": int(self.config.options.get("rows", 20)),
        }
        mailto = os.environ.get("OPENALEX_MAILTO", "")
        if mailto:
            params["mailto"] = mailto
        if plan.time_range is not None:
            params["filter"] = (
                f"from_publication_date:{plan.time_range.start_year}-01-01,"
                f"to_publication_date:{plan.time_range.end_year}-12-31"
            )

        url = f"{self.config.base_url.rstrip('/')}/works"
        data = await self._get_json(url, params=params)
        results = data.get("results", [])
        if not results:
            return []

        max_cites = max((int(r.get("cited_by_count", 0)) for r in results), default=0)
        refs: list[Reference] = []
        for i, w in enumerate(results):
            rel = w.get("relevance_score")
            if rel is None:
                # log-scaled citation fallback so a few giants don't crush the rest
                cites = int(w.get("cited_by_count", 0))
                rel = math.log1p(cites) / math.log1p(max_cites) if max_cites else 0.0
            else:
                rel = min(1.0, float(rel) / 100.0)
            refs.append(
                Reference(
                    id=f"openalex-{w.get('id', i).rsplit('/', 1)[-1]}",
                    doi=(w.get("doi") or "").replace("https://doi.org/", "") or None,
                    title=w.get("title") or "(untitled)",
                    year=int(w.get("publication_year") or 1900),
                    authors=_authors(w),
                    abstract=_deinvert(w.get("abstract_inverted_index")),
                    relevance_score=round(float(rel), 4),
                    source="openalex",
                    url=w.get("id"),
                )
            )
        return refs


def _authors(work: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for a in work.get("authorships", []) or []:
        name = (a.get("author") or {}).get("display_name")
        if name:
            out.append(name)
    return out


def _deinvert(index: dict[str, list[int]] | None) -> str | None:
    """Rebuild abstract text from OpenAlex's inverted index."""
    if not index:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in index.items():
        for p in positions:
            positioned.append((p, word))
    positioned.sort()
    return " ".join(w for _, w in positioned) or None

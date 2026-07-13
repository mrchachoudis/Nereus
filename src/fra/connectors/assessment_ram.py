"""RAM Legacy-style stock-assessment connector.

Consumes a configurable JSON endpoint serving RAM Legacy Stock Assessment
Database records (or a compatible export) with a documented schema:

    {"species": "Merluccius merluccius", "fao_area": "37.2", "gsa": "17"|null,
     "year": 2015, "ssb": 41000.0, "f": 0.62, "f_msy": 0.25, "b_msy": 80000.0,
     "status": "overfished"|null, "ref": "RAM:..."}

Missing reference points stay ``None`` so the status classifier returns
``"unknown"`` rather than guessing (DESIGN_PROMPT §9). Cite the RAM release DOI
per its CC-BY terms (see README).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from fra.connectors.base import ConnectorConfig, HttpConnector
from fra.models import AssessmentRecord, ResearchPlan, StockStatus
from fra.spatial import normalize_area
from fra.taxonomy import TaxonomyResolver

_VALID_STATUS = {"healthy", "overfished", "overfishing", "both", "unknown"}


class RamLegacyConnector(HttpConnector):
    domain = "assessment"

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        cache_dir: str | Path = ".fra_cache",
        client: httpx.AsyncClient | None = None,
        resolver: TaxonomyResolver | None = None,
    ) -> None:
        super().__init__(config, cache_dir=cache_dir, client=client)
        self._resolver = resolver or TaxonomyResolver(cache_dir, allow_network=False)

    async def fetch(self, plan: ResearchPlan) -> list[AssessmentRecord]:
        if plan.time_range is None:
            return []
        records: list[AssessmentRecord] = []
        url = f"{self.config.base_url.rstrip('/')}/assessments"
        for taxon in plan.taxa:
            for area in plan.areas:
                params: dict[str, Any] = {
                    "species": taxon.scientific_name,
                    "area": area.fao_area,
                    "start_year": plan.time_range.start_year,
                    "end_year": plan.time_range.end_year,
                }
                data = await self._get_json(url, params=params)
                records.extend(self._parse(data))
        return records

    def _parse(self, data: Any) -> list[AssessmentRecord]:
        rows = data.get("rows", data) if isinstance(data, dict) else data
        out: list[AssessmentRecord] = []
        for i, row in enumerate(rows or []):
            taxon = self._resolver.resolve(row["species"])
            area = normalize_area(str(row.get("fao_area", "")), gsa=row.get("gsa"))
            ref = row.get("ref") or f"{self.name}:{row['species']}:{row['fao_area']}:{row['year']}"
            out.append(
                AssessmentRecord(
                    id=f"{self.name}-{i}-{row['year']}",
                    taxon=taxon,
                    area=area,
                    year=int(row["year"]),
                    ssb=_opt_float(row.get("ssb")),
                    f_current=_opt_float(row.get("f")),
                    f_msy=_opt_float(row.get("f_msy")),
                    b_msy=_opt_float(row.get("b_msy")),
                    status=_coerce_status(row.get("status")),
                    source=self.name,
                    source_ref=ref,
                )
            )
        return out


def _opt_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _coerce_status(v: Any) -> StockStatus | None:
    if v is None:
        return None
    s = str(v).lower()
    return s if s in _VALID_STATUS else None  # type: ignore[return-value]

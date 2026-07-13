"""FAO-style landings connector.

FAO global production has no single stable JSON API, so this connector consumes a
configurable JSON endpoint (or an exported FishStatJ dataset served as JSON)
whose rows follow a documented, self-describing schema:

    {"species": "Merluccius merluccius", "fao_area": "37.2", "gsa": "17"|null,
     "year": 2015, "tonnes": 8421.0, "gear": "bottom trawl"|null,
     "ref": "FAO:..."}

Species names are resolved to WoRMS AphiaIDs and spatial codes normalized to FAO
areas at ingest. Point ``base_url`` at your regional portal in ``connectors.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from fra.connectors.base import ConnectorConfig, HttpConnector
from fra.models import LandingsRecord, ResearchPlan
from fra.spatial import normalize_area
from fra.taxonomy import TaxonomyResolver


class FaoLandingsConnector(HttpConnector):
    domain = "landings"

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

    async def fetch(self, plan: ResearchPlan) -> list[LandingsRecord]:
        if plan.time_range is None:
            return []
        records: list[LandingsRecord] = []
        url = f"{self.config.base_url.rstrip('/')}/query"
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

    def _parse(self, data: Any) -> list[LandingsRecord]:
        rows = data.get("rows", data) if isinstance(data, dict) else data
        out: list[LandingsRecord] = []
        for i, row in enumerate(rows or []):
            tonnes = row.get("tonnes")
            if tonnes is None:
                continue  # gaps are data — skip, never invent
            taxon = self._resolver.resolve(row["species"])
            area = normalize_area(str(row.get("fao_area", "")), gsa=row.get("gsa"))
            ref = row.get("ref") or f"{self.name}:{row['species']}:{row['fao_area']}:{row['year']}"
            out.append(
                LandingsRecord(
                    id=f"{self.name}-{i}-{row['year']}",
                    taxon=taxon,
                    area=area,
                    year=int(row["year"]),
                    tonnes=float(tonnes),
                    gear=row.get("gear"),
                    source=self.name,
                    source_ref=ref,
                )
            )
        return out

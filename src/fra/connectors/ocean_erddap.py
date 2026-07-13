"""ERDDAP oceanographic connector.

Pulls gridded environmental variables (SST, chlorophyll-a, ...) from an ERDDAP
server and aggregates them to the analysis resolution (annual means over the
plan's window). ERDDAP's ``.json`` response is a column table:

    {"table": {"columnNames": [...], "columnUnits": [...], "rows": [[...], ...]}}

Spatial bounds per FAO area come from ``connectors.yaml`` (``area_bounds``); if an
area has no configured bounding box the variable is skipped and the orchestrator
records a coverage gap rather than guessing coordinates.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from fra.connectors.base import HttpConnector
from fra.models import CovariateSeries, CovariateVariable, ResearchPlan, SpatialUnit, TimePoint


class ErddapConnector(HttpConnector):
    domain = "ocean"

    async def fetch(self, plan: ResearchPlan) -> list[CovariateSeries]:
        if plan.time_range is None:
            return []
        variables: dict[str, dict[str, str]] = self.config.options.get("variables", {})
        area_bounds: dict[str, dict[str, float]] = self.config.options.get("area_bounds", {})
        series: list[CovariateSeries] = []

        for area in plan.areas:
            bounds = _resolve_bounds(area, area_bounds)
            if bounds is None:
                continue
            for var_name, spec in variables.items():
                points = await self._fetch_variable(plan, area, var_name, spec, bounds)
                if not points:
                    continue
                series.append(
                    CovariateSeries(
                        id=f"erddap-{var_name}-{area.fao_area}",
                        variable=_as_variable(var_name),
                        area=area,
                        values=points,
                        unit=spec.get("unit", "unknown"),
                        source=self.name,
                        source_ref=f"{self.config.base_url}/{spec.get('dataset_id', var_name)}",
                    )
                )
        return series

    async def _fetch_variable(
        self,
        plan: ResearchPlan,
        area: SpatialUnit,
        var_name: str,
        spec: dict[str, str],
        bounds: dict[str, float],
    ) -> list[TimePoint]:
        assert plan.time_range is not None
        dataset = spec["dataset_id"]
        field = spec["field"]
        start = f"{plan.time_range.start_year}-01-01"
        end = f"{plan.time_range.end_year}-12-31"
        # ERDDAP griddap constraint syntax embedded in the path.
        query = (
            f"{field}[({start}):1:({end})]"
            f"[({bounds['lat_min']}):1:({bounds['lat_max']})]"
            f"[({bounds['lon_min']}):1:({bounds['lon_max']})]"
        )
        url = f"{self.config.base_url.rstrip('/')}/griddap/{dataset}.json"
        data = await self._get_json(url, params={"query": query})
        return _aggregate_annual(data, field)


def _resolve_bounds(
    area: SpatialUnit, area_bounds: dict[str, dict[str, float]]
) -> dict[str, float] | None:
    # exact code, then progressively coarser parent codes
    code = area.fao_area
    while code:
        if code in area_bounds:
            return area_bounds[code]
        if "." not in code:
            break
        code = code.rsplit(".", 1)[0]
    return None


def _aggregate_annual(data: dict[str, Any], field: str) -> list[TimePoint]:
    table = data.get("table", {})
    names: list[str] = table.get("columnNames", [])
    rows: list[list[Any]] = table.get("rows", [])
    if "time" not in names or field not in names:
        return []
    t_idx = names.index("time")
    v_idx = names.index(field)

    by_year: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        raw_t, raw_v = row[t_idx], row[v_idx]
        if raw_v is None:
            continue
        year = _year_of(raw_t)
        if year is not None:
            by_year[year].append(float(raw_v))

    points: list[TimePoint] = []
    for year in sorted(by_year):
        vals = by_year[year]
        points.append(TimePoint(date=date(year, 7, 1), value=statistics.fmean(vals), qc_flag=1))
    return points


def _year_of(raw_t: Any) -> int | None:
    if isinstance(raw_t, int | float):  # epoch seconds
        return datetime.utcfromtimestamp(float(raw_t)).year
    try:
        return datetime.fromisoformat(str(raw_t).replace("Z", "+00:00")).year
    except ValueError:
        return None


def _as_variable(name: str) -> CovariateVariable:
    allowed = {"sst", "chlor_a", "salinity", "upwelling_index", "sea_level_anomaly", "oxygen"}
    if name not in allowed:
        raise ValueError(f"unsupported covariate variable {name!r}")
    return name  # type: ignore[return-value]

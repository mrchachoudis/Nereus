"""Analysis agent: the deterministic quantitative barrier (DESIGN_PROMPT §3, §9).

Waits for retrieval, then runs trend tests, stock-status classification, and
covariate association over the Blackboard, emitting :class:`AnalysisResult`
objects. It performs no arithmetic itself beyond marshalling series - every
statistic comes from a pure, unit-tested function in :mod:`fra.analysis`. Each
result records the ``inputs`` (record IDs) it depends on so claims can be
grounded and audited.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fra.agents.base import BaseAgent, GuardResult
from fra.analysis import classify_status, lagged_association, mann_kendall
from fra.blackboard import Blackboard
from fra.models import (
    AnalysisResult,
    AssessmentRecord,
    CovariateSeries,
    LandingsRecord,
)

_MIN_POINTS = 5  # below this a trend/association test is not meaningful


class AnalysisAgent(BaseAgent):
    name = "analysis"

    def __init__(self, *, timeout_s: float = 300.0, alpha: float = 0.05) -> None:
        super().__init__(timeout_s=timeout_s, max_retries=1)
        self._alpha = alpha

    def _guard(self, bb: Blackboard) -> GuardResult:
        if bb.plan is None:
            return GuardResult(ok=False, missing=["plan"])
        if not bb.has_retrieval_data():
            return GuardResult(
                ok=False, missing=["any retrieval data (landings/assessments/ocean)"]
            )
        return GuardResult(ok=True)

    async def _run(self, bb: Blackboard) -> Blackboard:
        results: list[AnalysisResult] = []
        results.extend(self._landings_trends(bb.landings))
        results.extend(self._ssb_trends(bb.assessments))
        results.extend(self._status(bb.assessments))
        results.extend(self._associations(bb))
        bb.add_analyses(results)
        self.log.info("analysis produced %d results", len(results))
        return bb

    # -- trends --------------------------------------------------------------

    def _landings_trends(self, landings: list[LandingsRecord]) -> list[AnalysisResult]:
        out: list[AnalysisResult] = []
        for (taxon, area), recs in _group(landings).items():
            recs.sort(key=lambda r: r.year)
            if len(recs) < _MIN_POINTS:
                continue
            series = [r.tonnes for r in recs]
            tr = mann_kendall(series, alpha=self._alpha)
            out.append(
                AnalysisResult(
                    id=f"trend-landings-{_slug(taxon)}-{area}",
                    kind="trend",
                    target=f"Landings of {taxon} in {area} ({recs[0].year}-{recs[-1].year})",
                    statistic=tr.tau,
                    p_value=tr.p_value,
                    confidence_interval=tr.slope_ci,
                    effect_size=tr.slope,
                    interpretation=(
                        f"Landings show a {tr.trend} trend (Mann-Kendall p={tr.p_value:.3g}; "
                        f"Theil-Sen slope {tr.slope:.1f} tonnes/yr, "
                        f"95% CI [{tr.slope_ci[0]:.1f}, {tr.slope_ci[1]:.1f}])."
                    ),
                    inputs=[r.id for r in recs],
                    detail={"trend": tr.trend, "z": tr.z, "n": float(tr.n), "unit": "tonnes/yr"},
                )
            )
        return out

    def _ssb_trends(self, assessments: list[AssessmentRecord]) -> list[AnalysisResult]:
        out: list[AnalysisResult] = []
        for (taxon, area), recs in _group(assessments).items():
            usable = [r for r in recs if r.ssb is not None]
            usable.sort(key=lambda r: r.year)
            if len(usable) < _MIN_POINTS:
                continue
            series = [float(r.ssb) for r in usable if r.ssb is not None]
            tr = mann_kendall(series, alpha=self._alpha)
            out.append(
                AnalysisResult(
                    id=f"trend-ssb-{_slug(taxon)}-{area}",
                    kind="trend",
                    target=f"SSB of {taxon} in {area} ({usable[0].year}-{usable[-1].year})",
                    statistic=tr.tau,
                    p_value=tr.p_value,
                    confidence_interval=tr.slope_ci,
                    effect_size=tr.slope,
                    interpretation=(
                        f"Spawning stock biomass shows a {tr.trend} trend "
                        f"(Mann-Kendall p={tr.p_value:.3g}; Theil-Sen slope {tr.slope:.1f}/yr)."
                    ),
                    inputs=[r.id for r in usable],
                    detail={"trend": tr.trend, "z": tr.z, "n": float(tr.n)},
                )
            )
        return out

    # -- status --------------------------------------------------------------

    def _status(self, assessments: list[AssessmentRecord]) -> list[AnalysisResult]:
        out: list[AnalysisResult] = []
        for (taxon, area), recs in _group(assessments).items():
            recs.sort(key=lambda r: r.year)
            latest = recs[-1]
            k = classify_status(
                f_current=latest.f_current,
                f_msy=latest.f_msy,
                biomass=latest.ssb,
                b_msy=latest.b_msy,
            )
            ci = None
            interp = (
                f"As of {latest.year}, {taxon} in {area} is classified '{k.status}': {k.reason}."
            )
            if k.status == "unknown":
                interp += " Reference points are incomplete, so no Kobe classification is made."
            out.append(
                AnalysisResult(
                    id=f"status-{_slug(taxon)}-{area}",
                    kind="status_classification",
                    target=f"Stock status of {taxon} in {area} ({latest.year})",
                    statistic=(k.f_ratio if k.f_ratio is not None else float("nan")),
                    p_value=None,
                    confidence_interval=ci,
                    effect_size=k.b_ratio,
                    interpretation=interp,
                    inputs=[latest.id],
                    detail={
                        "status": k.status,
                        "b_ratio": _num(k.b_ratio),
                        "f_ratio": _num(k.f_ratio),
                        "year": float(latest.year),
                    },
                )
            )
        return out

    # -- associations --------------------------------------------------------

    def _associations(self, bb: Blackboard) -> list[AnalysisResult]:
        out: list[AnalysisResult] = []
        metrics = _metric_year_maps(bb)
        for cov in bb.covariates:
            cov_by_year = _covariate_year_map(cov)
            for metric_name, (year_map, input_ids) in metrics.items():
                years = sorted(set(cov_by_year) & set(year_map))
                if len(years) < _MIN_POINTS:
                    continue
                cov_vals = [cov_by_year[y] for y in years]
                met_vals = [year_map[y] for y in years]
                max_lag = min(4, len(years) - _MIN_POINTS)
                if max_lag < 1:
                    continue
                try:
                    lp = lagged_association(cov_vals, met_vals, max_lag=max_lag)
                except ValueError:
                    continue
                if lp.best_lag > 0:
                    phrase = f"{cov.variable.upper()} leads {metric_name} by {lp.best_lag} yr"
                elif lp.best_lag < 0:
                    phrase = f"{cov.variable.upper()} lags {metric_name} by {abs(lp.best_lag)} yr"
                else:
                    phrase = f"{cov.variable.upper()} is concurrent with {metric_name}"
                out.append(
                    AnalysisResult(
                        id=f"assoc-{cov.variable}-{metric_name}-{cov.area.fao_area}",
                        kind="association",
                        target=f"{cov.variable.upper()} vs {metric_name} in {cov.area.fao_area}",
                        statistic=lp.best_r,
                        p_value=lp.best_p_value_adjusted,
                        confidence_interval=None,
                        effect_size=lp.slope,
                        interpretation=(
                            f"{phrase} at peak association (r={lp.best_r:.2f}, "
                            f"Bonferroni-adjusted p={lp.best_p_value_adjusted:.3g}). {lp.caveat}"
                        ),
                        inputs=[cov.id, *input_ids],
                        detail={
                            "best_lag": float(lp.best_lag),
                            "r": lp.best_r,
                            "lags": lp.lags,
                            "correlations": lp.correlations,
                            "n_effective": float(lp.n_effective),
                        },
                    )
                )
        return out


# -- helpers -----------------------------------------------------------------


def _group(records: list[Any]) -> dict[tuple[str, str], list[Any]]:
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for r in records:
        grouped[(r.taxon.scientific_name, r.area.fao_area)].append(r)
    return grouped


def _metric_year_maps(bb: Blackboard) -> dict[str, tuple[dict[int, float], list[str]]]:
    """Map metric name -> ({year: value}, [record ids]) for association inputs."""
    out: dict[str, tuple[dict[int, float], list[str]]] = {}
    if bb.landings:
        ym: dict[int, float] = {}
        ids: list[str] = []
        for r in sorted(bb.landings, key=lambda r: r.year):
            ym[r.year] = r.tonnes
            ids.append(r.id)
        out["landings"] = (ym, ids)
    if bb.assessments:
        ym2: dict[int, float] = {}
        ids2: list[str] = []
        for a in sorted(bb.assessments, key=lambda r: r.year):
            if a.ssb is not None:
                ym2[a.year] = a.ssb
                ids2.append(a.id)
        if ym2:
            out["SSB"] = (ym2, ids2)
    return out


def _covariate_year_map(cov: CovariateSeries) -> dict[int, float]:
    return {tp.date.year: tp.value for tp in cov.good_values() if tp.value is not None}


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _num(v: float | None) -> float:
    return float(v) if v is not None else float("nan")

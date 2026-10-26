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

import numpy as np

from fra.agents.base import BaseAgent, GuardResult
from fra.analysis import LagProfile, classify_status, lagged_association, mann_kendall
from fra.blackboard import Blackboard
from fra.models import (
    AnalysisResult,
    AssessmentRecord,
    CovariateSeries,
    LandingsRecord,
)

_MIN_POINTS = 5  # below this a trend/association test is not meaningful
_SHORT_SERIES = 15  # below this, tests run but have low power; flagged as a caveat
_STRONG_RAW_R = 0.5  # raw |r| above this that vanishes after detrending is trend-driven


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
                    caveats=_short_trend_caveat(f"Landings trend in {area}", tr.n),
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
                    caveats=_short_trend_caveat(f"SSB trend in {area}", tr.n),
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
            caveats: list[str] = []
            if k.status == "unknown":
                interp += " Reference points are incomplete, so no Kobe classification is made."
            else:
                caveats.append(
                    f"Stock status in {area} is based on the terminal assessment year "
                    f"({latest.year}) only, with no uncertainty on B/B_MSY or F/F_MSY."
                )
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
                    caveats=caveats,
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
                target = f"{cov.variable.upper()} vs {metric_name} in {cov.area.fao_area}"
                out.append(
                    AnalysisResult(
                        id=f"assoc-{cov.variable}-{metric_name}-{cov.area.fao_area}",
                        kind="association",
                        target=target,
                        statistic=lp.best_r,
                        p_value=lp.best_p_value_adjusted,
                        confidence_interval=None,
                        effect_size=lp.slope,
                        interpretation=self._association_text(cov.variable, metric_name, lp),
                        inputs=[cov.id, *input_ids],
                        caveats=self._association_caveats(target, lp),
                        detail={
                            "best_lag": float(lp.best_lag),
                            "r": lp.best_r,
                            "raw_r": lp.raw_r,
                            "detrend": lp.detrend,
                            "lags": lp.lags,
                            "correlations": lp.correlations,
                            "p_values": lp.p_values,
                            "n_obs": float(lp.n_obs),
                            "n_effective": lp.n_effective,
                        },
                    )
                )
        return out

    def _association_text(self, variable: str, metric_name: str, lp: LagProfile) -> str:
        cov = variable.upper()
        if lp.best_lag > 0:
            where = f"with {cov} leading {metric_name} by {lp.best_lag} yr"
        elif lp.best_lag < 0:
            where = f"with {cov} lagging {metric_name} by {abs(lp.best_lag)} yr"
        else:
            where = "with no lag"
        stats_text = (
            f"detrended r={lp.best_r:.2f}, Bonferroni-adjusted p={lp.best_p_value_adjusted:.3g}"
        )
        if lp.best_p_value_adjusted < self._alpha:
            head = f"{cov} and {metric_name} are associated at peak {where} ({stats_text})."
        else:
            head = (
                f"No significant association between {cov} and {metric_name} after "
                f"detrending; the strongest lag is {where} ({stats_text})."
            )
        return f"{head} {lp.caveat}"

    def _association_caveats(self, target: str, lp: LagProfile) -> list[str]:
        caveats: list[str] = []
        significant = lp.best_p_value_adjusted < self._alpha
        if abs(lp.raw_r) >= _STRONG_RAW_R and np.sign(lp.raw_r) != np.sign(lp.best_r):
            caveats.append(
                f"{target}: the raw correlation (r={lp.raw_r:.2f}) reverses sign after "
                f"detrending (r={lp.best_r:.2f}), so it reflects shared trends, not "
                "year-to-year co-variation."
            )
        elif abs(lp.raw_r) >= _STRONG_RAW_R and not significant:
            caveats.append(
                f"{target}: the raw correlation (r={lp.raw_r:.2f}) is not significant after "
                f"detrending (adjusted p={lp.best_p_value_adjusted:.3g}), so it mostly "
                "reflects shared trends."
            )
        if lp.n_obs < _SHORT_SERIES:
            caveats.append(
                f"{target}: only {lp.n_obs} aligned years, and the lag was picked after the "
                "fact, so the test has low power and the best lag is unstable."
            )
        if lp.n_effective < 0.6 * lp.n_obs:
            caveats.append(
                f"{target}: strong autocorrelation leaves an effective sample size of "
                f"N*={lp.n_effective:.1f} out of {lp.n_obs} years."
            )
        return caveats


# -- helpers -----------------------------------------------------------------


def _short_trend_caveat(label: str, n: int) -> list[str]:
    if n >= _SHORT_SERIES:
        return []
    return [f"{label}: only {n} years, so Mann-Kendall has low power to detect a trend."]


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

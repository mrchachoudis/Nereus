"""Lagged association between an environmental covariate and a stock metric.

Computes the cross-correlation of ``covariate`` against ``metric`` over a range
of lags, reports the lag maximizing |r| together with the full lag profile, and
fits a simple linear model at the best lag. This layer is strictly
*associational*: the interpretation text must never use causal language, and the
best-lag correlation is flagged with a multiple-comparison caveat because it is
selected post hoc over many lags (DESIGN_PROMPT §9).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
from scipy import stats

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class LagProfile:
    """Cross-correlation result across lags plus the best-lag linear fit.

    A positive ``best_lag`` means the covariate *leads* the metric by that many
    steps (covariate at t associates with metric at t+lag).
    """

    best_lag: int
    best_r: float
    best_p_value: float
    best_p_value_adjusted: float  # Bonferroni over the number of lags tested
    lags: list[int]
    correlations: list[float]
    p_values: list[float]
    slope: float
    intercept: float
    n_effective: int
    caveat: str
    detail: dict[str, float] = field(default_factory=dict)


def _align(covariate: FloatArray, metric: FloatArray, lag: int) -> tuple[FloatArray, FloatArray]:
    """Align series at ``lag``; positive lag = covariate leads metric."""
    if lag > 0:
        return covariate[:-lag], metric[lag:]
    if lag < 0:
        return covariate[-lag:], metric[:lag]
    return covariate, metric


def lagged_association(
    covariate: Sequence[float],
    metric: Sequence[float],
    *,
    max_lag: int = 4,
    min_overlap: int = 5,
) -> LagProfile:
    """Cross-correlate ``covariate`` vs ``metric`` over ``±max_lag`` steps.

    Both series must be equal length and evenly spaced. Returns the lag with the
    largest absolute Pearson correlation, the full profile, and a Bonferroni
    adjustment over the number of lags tested. Raises ``ValueError`` if the
    series are too short to leave ``min_overlap`` aligned points at ``max_lag``.
    """
    cov = np.asarray(list(covariate), dtype=float)
    met = np.asarray(list(metric), dtype=float)
    if cov.size != met.size:
        raise ValueError(f"series length mismatch: {cov.size} vs {met.size}")
    if not (np.all(np.isfinite(cov)) and np.all(np.isfinite(met))):
        raise ValueError("association input contains non-finite values")
    if cov.size - max_lag < min_overlap:
        raise ValueError(
            f"series too short: {cov.size} points cannot support lag {max_lag} "
            f"with min_overlap {min_overlap}"
        )

    lags: list[int] = []
    corrs: list[float] = []
    pvals: list[float] = []
    for lag in range(-max_lag, max_lag + 1):
        a, b = _align(cov, met, lag)
        if a.size < min_overlap or np.std(a) == 0 or np.std(b) == 0:
            continue
        r, p = stats.pearsonr(a, b)
        lags.append(lag)
        corrs.append(float(r))
        pvals.append(float(p))

    if not lags:
        raise ValueError("no lag produced a valid (non-degenerate) overlap")

    best_idx = int(np.argmax(np.abs(corrs)))
    best_lag = lags[best_idx]
    best_r = corrs[best_idx]
    best_p = pvals[best_idx]
    n_tested = len(lags)
    best_p_adj = float(min(1.0, best_p * n_tested))

    a, b = _align(cov, met, best_lag)
    lin = stats.linregress(a, b)

    caveat = (
        f"Associational only — not causal. Best lag selected post hoc over "
        f"{n_tested} lags; reported p adjusted by Bonferroni (×{n_tested}). "
        f"n={a.size} aligned observations."
    )

    return LagProfile(
        best_lag=best_lag,
        best_r=best_r,
        best_p_value=best_p,
        best_p_value_adjusted=best_p_adj,
        lags=lags,
        correlations=corrs,
        p_values=pvals,
        slope=float(lin.slope),
        intercept=float(lin.intercept),
        n_effective=int(a.size),
        caveat=caveat,
        detail={"r_squared": float(lin.rvalue**2), "std_err": float(lin.stderr)},
    )

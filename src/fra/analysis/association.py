"""Lagged association between an environmental covariate and a stock metric.

Computes the cross-correlation of ``covariate`` against ``metric`` over a range
of lags, reports the lag with the lowest p-value together with the full lag
profile, and
fits a simple linear model at the best lag. This layer is strictly
*associational*: the interpretation text must never use causal language, and the
best-lag correlation is flagged with a multiple-comparison caveat because it is
selected post hoc over many lags (DESIGN_PROMPT §9).

Two corrections guard against spurious correlation between fisheries time series:

* **Detrending.** Two series that both trend (warming SST, declining SSB) are
  strongly correlated whether or not they are related. Both series are detrended
  (linear fit removed, or first-differenced) before correlating, and the raw
  correlation at the chosen lag is kept only for comparison.
* **Effective sample size.** Annual series are autocorrelated, so their points are
  not independent. The p-value uses the effective number of observations N* from
  Pyper & Peterman (1998, Can. J. Fish. Aquat. Sci. 55:2127-2140) rather than N.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy import stats

FloatArray = npt.NDArray[np.float64]
DetrendMethod = Literal["linear", "difference", "none"]

# N* is never allowed below this: a t-test needs df = N* - 2 > 0.
_MIN_N_EFFECTIVE = 3.0


@dataclass(frozen=True)
class LagProfile:
    """Cross-correlation result across lags plus the best-lag linear fit.

    A positive ``best_lag`` means the covariate *leads* the metric by that many
    steps (covariate at t associates with metric at t+lag). ``best_r`` and all
    p-values refer to the detrended series; ``raw_r`` is the correlation of the
    untransformed series at the same lag, kept to show how much of it was trend.
    """

    best_lag: int
    best_r: float
    best_p_value: float  # from a t-test on N* (effective) observations
    best_p_value_adjusted: float  # Bonferroni over the number of lags tested
    lags: list[int]
    correlations: list[float]
    p_values: list[float]
    slope: float
    intercept: float
    n_obs: int  # aligned observations at the best lag
    n_effective: float  # Pyper & Peterman N* at the best lag
    raw_r: float
    detrend: DetrendMethod
    caveat: str
    detail: dict[str, float] = field(default_factory=dict)


def _align(covariate: FloatArray, metric: FloatArray, lag: int) -> tuple[FloatArray, FloatArray]:
    """Align series at ``lag``; positive lag = covariate leads metric."""
    if lag > 0:
        return covariate[:-lag], metric[lag:]
    if lag < 0:
        return covariate[-lag:], metric[:lag]
    return covariate, metric


def remove_trend(values: FloatArray, method: DetrendMethod) -> FloatArray:
    """Remove a linear trend or first-difference a series.

    ``"difference"`` returns one fewer point than it was given.
    """
    if method == "none":
        return values
    if method == "difference":
        return np.diff(values)
    t = np.arange(values.size, dtype=float)
    slope, intercept = np.polyfit(t, values, 1)
    return np.asarray(values - (slope * t + intercept), dtype=float)


def autocorrelation(values: FloatArray, lag: int) -> float:
    """Sample autocorrelation at ``lag``, with the N/(N-lag) small-sample scaling.

    This is the estimator Pyper & Peterman (1998) use for N*.
    """
    n = values.size
    if lag <= 0 or lag >= n:
        raise ValueError(f"lag must be in [1, {n - 1}], got {lag}")
    dev = values - values.mean()
    denom = float(np.sum(dev**2))
    if denom == 0.0:
        return 0.0
    num = float(np.sum(dev[:-lag] * dev[lag:]))
    return (n / (n - lag)) * num / denom


def effective_n(x: FloatArray, y: FloatArray) -> float:
    """Effective number of independent observations for a correlation of x and y.

    Pyper & Peterman (1998), eq. 1::

        1/N* = 1/N + (2/N) * sum_{j=1}^{N/5} rho_xx(j) * rho_yy(j)

    The result is clamped to ``[3, N]``: negative cross-products could otherwise
    push N* above N, and the t-test needs at least one degree of freedom.
    """
    n = x.size
    if n != y.size:
        raise ValueError(f"series length mismatch: {n} vs {y.size}")
    max_j = max(1, n // 5)
    total = sum(autocorrelation(x, j) * autocorrelation(y, j) for j in range(1, max_j + 1))
    inv = 1.0 / n + (2.0 / n) * total
    n_star = n if inv <= 0 else 1.0 / inv
    return float(min(float(n), max(_MIN_N_EFFECTIVE, n_star)))


def correlation_p_value(r: float, n_eff: float) -> float:
    """Two-sided p-value for Pearson r tested on ``n_eff`` observations."""
    df = n_eff - 2.0
    if df <= 0:
        return 1.0
    if abs(r) >= 1.0:
        return 0.0
    t = abs(r) * np.sqrt(df / (1.0 - r * r))
    return float(min(1.0, 2.0 * stats.t.sf(t, df)))


def lagged_association(
    covariate: Sequence[float],
    metric: Sequence[float],
    *,
    max_lag: int = 4,
    min_overlap: int = 5,
    detrend: DetrendMethod = "linear",
) -> LagProfile:
    """Cross-correlate ``covariate`` vs ``metric`` over ``±max_lag`` steps.

    Both series must be equal length and evenly spaced. Each is detrended first
    (see :func:`remove_trend`). Returns the lag with the lowest p-value (ties broken
    by larger |r|, then the shorter lag), the full profile, p-values based on the effective sample size,
    and a Bonferroni adjustment over the number of lags tested. Raises
    ``ValueError`` if the series are too short to leave ``min_overlap`` aligned
    points at ``max_lag``.
    """
    cov_raw = np.asarray(list(covariate), dtype=float)
    met_raw = np.asarray(list(metric), dtype=float)
    if cov_raw.size != met_raw.size:
        raise ValueError(f"series length mismatch: {cov_raw.size} vs {met_raw.size}")
    if not (np.all(np.isfinite(cov_raw)) and np.all(np.isfinite(met_raw))):
        raise ValueError("association input contains non-finite values")

    cov = remove_trend(cov_raw, detrend)
    met = remove_trend(met_raw, detrend)
    if cov.size - max_lag < min_overlap:
        raise ValueError(
            f"series too short: {cov.size} points (after detrend={detrend!r}) cannot "
            f"support lag {max_lag} with min_overlap {min_overlap}"
        )

    lags: list[int] = []
    corrs: list[float] = []
    pvals: list[float] = []
    n_stars: list[float] = []
    for lag in range(-max_lag, max_lag + 1):
        a, b = _align(cov, met, lag)
        if a.size < min_overlap or np.std(a) == 0 or np.std(b) == 0:
            continue
        r = float(stats.pearsonr(a, b)[0])
        n_star = effective_n(a, b)
        lags.append(lag)
        corrs.append(r)
        pvals.append(correlation_p_value(r, n_star))
        n_stars.append(n_star)

    if not lags:
        raise ValueError("no lag produced a valid (non-degenerate) overlap")

    # Pick by p-value, not by |r|: longer lags leave fewer overlapping years, and
    # r from fewer points is noisier, so the largest |r| tends to sit at the
    # extreme lag. The p-value accounts for the overlap (via N*).
    best_idx = min(range(len(lags)), key=lambda i: (pvals[i], -abs(corrs[i]), abs(lags[i])))
    best_lag = lags[best_idx]
    best_r = corrs[best_idx]
    best_p = pvals[best_idx]
    n_star_best = n_stars[best_idx]
    n_tested = len(lags)
    best_p_adj = float(min(1.0, best_p * n_tested))

    a, b = _align(cov, met, best_lag)
    lin = stats.linregress(a, b)

    # Raw correlation at the same lag, on the untransformed series. Differencing
    # drops the first point, so align the raw series from index 1 in that case.
    offset = 1 if detrend == "difference" else 0
    ra, rb = _align(cov_raw[offset:], met_raw[offset:], best_lag)
    raw_r = float(stats.pearsonr(ra, rb)[0]) if np.std(ra) > 0 and np.std(rb) > 0 else 0.0

    method_text = {
        "linear": "both series linearly detrended",
        "difference": "both series first-differenced",
        "none": "no detrending",
    }[detrend]
    caveat = (
        f"Associational only - not causal. {method_text.capitalize()} before correlating "
        f"(raw r={raw_r:.2f}). Lag with the lowest p selected post hoc over {n_tested} "
        f"lags; reported p uses an autocorrelation-adjusted effective sample size (N*={n_star_best:.1f} of "
        f"n={a.size}) and is Bonferroni-adjusted (×{n_tested})."
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
        n_obs=int(a.size),
        n_effective=n_star_best,
        raw_r=raw_r,
        detrend=detrend,
        caveat=caveat,
        detail={"r_squared": float(lin.rvalue**2), "std_err": float(lin.stderr)},
    )

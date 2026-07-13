"""Monotonic-trend detection: Mann-Kendall + Theil-Sen slope.

Pure functions over sequences of floats. The Mann-Kendall implementation uses the
standard normal approximation with the continuity correction and a tie
correction on the variance; the seasonal variant (Hirsch-Slack) sums the S
statistic and its variance across seasons, which is the right test for monthly
series carrying an annual cycle. Theil-Sen gives a robust slope with a
distribution-free confidence interval.

References: Mann (1945), Kendall (1975), Hirsch & Slack (1984), Sen (1968).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
from scipy import stats

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class TrendResult:
    """Outcome of a trend test.

    ``trend`` is the qualitative call at ``alpha``. ``s`` is the Mann-Kendall S
    statistic, ``z`` its normal score, ``p_value`` two-sided. ``slope`` is the
    Theil-Sen estimate in units-per-step, with ``slope_ci`` its two-sided CI.
    """

    trend: str  # "increasing" | "decreasing" | "no trend"
    s: float
    z: float
    p_value: float
    tau: float
    slope: float
    slope_ci: tuple[float, float]
    n: int
    alpha: float
    detail: dict[str, float] = field(default_factory=dict)


def _tie_correction(values: FloatArray) -> float:
    """Variance tie-correction term: sum over tie groups of t(t-1)(2t+5)."""
    _, counts = np.unique(values, return_counts=True)
    ties = counts[counts > 1]
    return float(np.sum(ties * (ties - 1) * (2 * ties + 5)))


def _mk_s_and_var(values: FloatArray) -> tuple[float, float]:
    """Mann-Kendall S statistic and its tie-corrected variance."""
    n = len(values)
    # S = sum over i<j of sign(x_j - x_i). np.subtract.outer(v, v)[i, j] = x_i - x_j,
    # so for the upper triangle (i < j) we negate to get sign(x_j - x_i).
    diff = np.subtract.outer(values, values)
    s = float(-np.sum(np.sign(diff[np.triu_indices(n, k=1)])))
    var = (n * (n - 1) * (2 * n + 5) - _tie_correction(values)) / 18.0
    return s, var


def _z_from_s(s: float, var: float) -> float:
    """Normal score with continuity correction; guards zero variance."""
    if var <= 0:
        return 0.0
    if s > 0:
        return (s - 1) / math.sqrt(var)
    if s < 0:
        return (s + 1) / math.sqrt(var)
    return 0.0


def mann_kendall(
    values: Sequence[float],
    *,
    alpha: float = 0.05,
) -> TrendResult:
    """Non-seasonal Mann-Kendall trend test plus Theil-Sen slope.

    ``values`` must be evenly spaced in time and ordered chronologically. Raises
    ``ValueError`` for fewer than 4 points (the normal approximation is
    meaningless below that).
    """
    arr = np.asarray(list(values), dtype=float)
    n = arr.size
    if n < 4:
        raise ValueError(f"Mann-Kendall needs >= 4 points, got {n}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("Mann-Kendall input contains non-finite values")

    s, var = _mk_s_and_var(arr)
    z = _z_from_s(s, var)
    p = 2.0 * (1.0 - stats.norm.cdf(abs(z)))
    p = float(min(1.0, p))

    tau, _ = stats.kendalltau(np.arange(n), arr)
    ts = stats.theilslopes(arr, np.arange(n), alpha=1 - alpha)
    slope, lo, hi = float(ts[0]), float(ts[2]), float(ts[3])

    if p < alpha and z > 0:
        trend = "increasing"
    elif p < alpha and z < 0:
        trend = "decreasing"
    else:
        trend = "no trend"

    return TrendResult(
        trend=trend,
        s=s,
        z=float(z),
        p_value=p,
        tau=float(tau) if tau == tau else 0.0,  # guard NaN
        slope=slope,
        slope_ci=(lo, hi),
        n=n,
        alpha=alpha,
        detail={"variance": var, "intercept": float(ts[1])},
    )


def seasonal_mann_kendall(
    values: Sequence[float],
    *,
    period: int = 12,
    alpha: float = 0.05,
) -> TrendResult:
    """Seasonal (Hirsch-Slack) Mann-Kendall for series with a fixed cycle.

    Splits the series into ``period`` seasons (e.g. 12 months), computes S and
    variance within each, and sums them - this removes the seasonal cycle's
    contribution to apparent trend. Theil-Sen slope is computed across matching
    seasons and pooled (median of per-season slopes).
    """
    arr = np.asarray(list(values), dtype=float)
    n = arr.size
    if n < period * 2:
        raise ValueError(f"seasonal Mann-Kendall needs >= 2 full periods ({period * 2}), got {n}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("seasonal Mann-Kendall input contains non-finite values")

    total_s = 0.0
    total_var = 0.0
    season_slopes: list[float] = []
    for season in range(period):
        seas = arr[season::period]
        if seas.size < 2:
            continue
        s, var = _mk_s_and_var(seas)
        total_s += s
        total_var += var
        if seas.size >= 3:
            ts = stats.theilslopes(seas, np.arange(seas.size), alpha=1 - alpha)
            # per-step here means per-period; multiply back to per-single-step scale.
            season_slopes.append(float(ts[0]) / period)

    z = _z_from_s(total_s, total_var)
    p = float(min(1.0, 2.0 * (1.0 - stats.norm.cdf(abs(z)))))

    slope = float(np.median(season_slopes)) if season_slopes else 0.0
    slope_sd = float(np.std(season_slopes)) if len(season_slopes) > 1 else 0.0
    slope_ci = (slope - 1.96 * slope_sd, slope + 1.96 * slope_sd)

    tau, _ = stats.kendalltau(np.arange(n), arr)

    if p < alpha and z > 0:
        trend = "increasing"
    elif p < alpha and z < 0:
        trend = "decreasing"
    else:
        trend = "no trend"

    return TrendResult(
        trend=trend,
        s=total_s,
        z=float(z),
        p_value=p,
        tau=float(tau) if tau == tau else 0.0,
        slope=slope,
        slope_ci=slope_ci,
        n=n,
        alpha=alpha,
        detail={"variance": total_var, "period": float(period), "n_seasons": float(period)},
    )


def theil_sen(
    y: Sequence[float],
    x: Sequence[float] | None = None,
    *,
    alpha: float = 0.05,
) -> tuple[float, float, tuple[float, float]]:
    """Theil-Sen robust slope. Returns ``(slope, intercept, (lo, hi))``."""
    yy = np.asarray(list(y), dtype=float)
    xx = np.arange(yy.size, dtype=float) if x is None else np.asarray(list(x), dtype=float)
    if yy.size < 3:
        raise ValueError(f"Theil-Sen needs >= 3 points, got {yy.size}")
    res = stats.theilslopes(yy, xx, alpha=1 - alpha)
    return float(res[0]), float(res[1]), (float(res[2]), float(res[3]))

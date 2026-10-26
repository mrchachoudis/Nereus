"""Tests for the deterministic analysis layer on synthetic data (DESIGN_PROMPT §9).

Each test injects a series with a *known* property (monotone trend, known lag,
known Kobe quadrant) and asserts the estimator recovers it.
"""

from __future__ import annotations

import numpy as np
import pytest

from fra.analysis import (
    classify_status,
    lagged_association,
    mann_kendall,
    seasonal_mann_kendall,
    theil_sen,
)
from fra.analysis.association import autocorrelation, correlation_p_value, effective_n


def _rng() -> np.random.Generator:
    return np.random.default_rng(1234)


def test_mann_kendall_detects_increasing() -> None:
    y = list(np.arange(30) * 0.5 + _rng().normal(0, 0.2, 30))
    r = mann_kendall(y)
    assert r.trend == "increasing"
    assert r.p_value < 0.01
    assert r.slope > 0
    lo, hi = r.slope_ci
    assert lo < r.slope < hi


def test_mann_kendall_detects_decreasing() -> None:
    y = list(-np.arange(30) * 0.5 + _rng().normal(0, 0.2, 30))
    r = mann_kendall(y)
    assert r.trend == "decreasing"
    assert r.slope < 0


def test_mann_kendall_no_trend_on_noise() -> None:
    y = list(_rng().normal(0, 1, 50))
    r = mann_kendall(y)
    assert r.trend == "no trend"
    assert r.p_value > 0.05


def test_mann_kendall_slope_magnitude() -> None:
    # exact line, slope 2.0 per step
    y = list(2.0 * np.arange(10) + 5.0)
    r = mann_kendall(y)
    assert r.slope == pytest.approx(2.0, abs=1e-9)
    assert r.detail["intercept"] == pytest.approx(5.0, abs=1e-9)


def test_mann_kendall_rejects_short_series() -> None:
    with pytest.raises(ValueError):
        mann_kendall([1.0, 2.0, 3.0])


def test_mann_kendall_rejects_non_finite() -> None:
    with pytest.raises(ValueError):
        mann_kendall([1.0, 2.0, float("nan"), 4.0, 5.0])


def test_seasonal_mann_kendall_removes_cycle() -> None:
    t = np.arange(60)
    # strong annual cycle, mild upward drift
    y = 0.03 * t + 5 * np.sin(2 * np.pi * t / 12) + _rng().normal(0, 0.1, 60)
    r = seasonal_mann_kendall(list(y), period=12)
    assert r.trend == "increasing"
    assert r.slope > 0


def test_seasonal_flat_series_no_trend() -> None:
    t = np.arange(48)
    y = 5 * np.sin(2 * np.pi * t / 12) + _rng().normal(0, 0.1, 48)
    r = seasonal_mann_kendall(list(y), period=12)
    assert r.trend == "no trend"


def test_theil_sen_matches_line() -> None:
    y = [3.0 * i - 1.0 for i in range(15)]
    slope, intercept, (lo, hi) = theil_sen(y)
    assert slope == pytest.approx(3.0)
    assert intercept == pytest.approx(-1.0)
    assert lo <= slope <= hi


def test_association_recovers_positive_lag() -> None:
    rng = _rng()
    cov = rng.normal(0, 1, 40)
    metric = np.empty(40)
    metric[:3] = rng.normal(0, 1, 3)
    metric[3:] = 1.5 * cov[:-3] + rng.normal(0, 0.05, 37)  # metric lags cov by 3
    lp = lagged_association(list(cov), list(metric), max_lag=5)
    assert lp.best_lag == 3
    assert lp.best_r > 0.9
    assert lp.best_p_value_adjusted <= lp.best_p_value * len(lp.lags) + 1e-12
    assert "not causal" in lp.caveat.lower()


def test_association_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        lagged_association([1.0, 2.0, 3.0], [1.0, 2.0])


def test_association_rejects_too_short() -> None:
    with pytest.raises(ValueError):
        lagged_association([1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0], max_lag=4)


def test_status_healthy() -> None:
    k = classify_status(f_current=0.2, f_msy=0.3, biomass=120, b_msy=100)
    assert k.status == "healthy"
    assert k.b_ratio == pytest.approx(1.2)
    assert k.f_ratio == pytest.approx(2 / 3)


def test_status_overfished_and_overfishing() -> None:
    assert classify_status(f_current=0.2, f_msy=0.3, biomass=50, b_msy=100).status == "overfished"
    assert classify_status(f_current=0.6, f_msy=0.3, biomass=120, b_msy=100).status == "overfishing"
    assert classify_status(f_current=0.6, f_msy=0.3, biomass=50, b_msy=100).status == "both"


def test_status_unknown_when_reference_missing() -> None:
    k = classify_status(f_current=0.5, f_msy=None, biomass=50, b_msy=100)
    assert k.status == "unknown"
    assert "F/F_MSY" in k.reason


def test_status_direct_ratios() -> None:
    k = classify_status(
        f_current=None,
        f_msy=None,
        biomass=None,
        b_msy=None,
        b_ratio_direct=0.8,
        f_ratio_direct=1.2,
    )
    assert k.status == "both"


# -- spurious correlation guards ------------------------------------------------


def test_association_independent_trends_not_significant() -> None:
    """Two unrelated series that both trend must not come out as associated."""
    rng = _rng()
    t = np.arange(30)
    sst = 18.0 + 0.1 * t + rng.normal(0, 0.3, 30)
    ssb = 50000.0 - 900.0 * t + rng.normal(0, 1500, 30)

    from scipy import stats

    # Naive Pearson on the raw series (the old behaviour) calls this a sure thing.
    r_naive, p_naive = stats.pearsonr(sst, ssb)
    assert r_naive < -0.9
    assert p_naive < 1e-6

    # Without detrending, the effective-n correction alone already removes it.
    raw = lagged_association(list(sst), list(ssb), max_lag=4, detrend="none")
    assert raw.n_effective < 10
    assert raw.best_p_value_adjusted > 0.05

    lp = lagged_association(list(sst), list(ssb), max_lag=4)
    assert lp.raw_r < -0.8  # raw r at the chosen lag is still trend-dominated
    assert lp.best_p_value_adjusted > 0.05
    assert "detrended" in lp.caveat


def test_association_recovers_lag_under_trends() -> None:
    """A real lagged signal on top of unrelated trends is still found."""
    rng = _rng()
    t = np.arange(40)
    signal = rng.normal(0, 1, 40)
    cov = 0.05 * t + signal
    metric = np.empty(40)
    metric[:2] = rng.normal(0, 1, 2)
    metric[2:] = 1.2 * signal[:-2] + rng.normal(0, 0.2, 38)
    metric -= 0.3 * t
    lp = lagged_association(list(cov), list(metric), max_lag=4)
    assert lp.best_lag == 2
    assert lp.best_r > 0.8
    assert lp.best_p_value_adjusted < 0.01


def test_association_first_difference() -> None:
    rng = _rng()
    cov = np.cumsum(rng.normal(0, 1, 30))
    metric = 2.0 * cov + rng.normal(0, 0.1, 30)
    lp = lagged_association(list(cov), list(metric), max_lag=3, detrend="difference")
    assert lp.detrend == "difference"
    assert lp.best_lag == 0
    assert lp.n_obs == 29  # differencing drops one point
    assert lp.best_r > 0.9


def test_effective_n_white_noise_close_to_n() -> None:
    rng = _rng()
    x, y = rng.normal(0, 1, 200), rng.normal(0, 1, 200)
    assert effective_n(x, y) > 150


def test_effective_n_shrinks_for_autocorrelated_series() -> None:
    rng = _rng()

    def ar1(phi: float, n: int) -> np.ndarray:
        out = np.zeros(n)
        for i in range(1, n):
            out[i] = phi * out[i - 1] + rng.normal()
        return out

    n_star = effective_n(ar1(0.9, 200), ar1(0.9, 200))
    assert 3.0 <= n_star < 100


def test_effective_n_bounds() -> None:
    x = np.array([1.0, -1.0] * 5)  # alternating: negative autocorrelation
    assert effective_n(x, x) <= x.size
    smooth = np.linspace(0, 1, 10) ** 2
    assert effective_n(smooth, smooth) >= 3.0


def test_correlation_p_value_matches_scipy_at_full_n() -> None:
    from scipy import stats

    rng = _rng()
    a, b = rng.normal(0, 1, 25), rng.normal(0, 1, 25)
    b = 0.5 * a + b
    r, p = stats.pearsonr(a, b)
    assert correlation_p_value(float(r), 25.0) == pytest.approx(float(p), rel=1e-6)


def test_autocorrelation_rejects_bad_lag() -> None:
    with pytest.raises(ValueError):
        autocorrelation(np.arange(5, dtype=float), 5)


def test_association_picks_lowest_p_not_largest_r() -> None:
    """Max |r| favours the longest lags (fewest points); lowest p does not."""
    hits_new = hits_old = 0
    for seed in range(300):
        rng = np.random.default_rng(seed)
        x = rng.normal(0, 1, 12)
        y = 0.8 * x + rng.normal(0, 1, 12)  # true association at lag 0
        lp = lagged_association(list(x), list(y), max_lag=4)
        assert lp.best_p_value == min(lp.p_values)
        old_lag = lp.lags[int(np.argmax(np.abs(lp.correlations)))]
        hits_new += lp.best_lag == 0
        hits_old += old_lag == 0
    assert hits_new > hits_old

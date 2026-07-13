"""Stock-status classification against reference points (Kobe quadrants).

Given fishing-mortality and biomass ratios relative to their MSY reference
points, place a stock in the Kobe plot:

    B/B_MSY  ≥ 1        < 1
    ───────────────────────────────
    F/F_MSY  ≤ 1  healthy      overfished
    F/F_MSY  > 1  overfishing  both

If a required reference point is missing, the result is ``"unknown"`` - we never
guess a status from incomplete inputs (DESIGN_PROMPT §9).
"""

from __future__ import annotations

from dataclasses import dataclass

from fra.models import StockStatus


@dataclass(frozen=True)
class KobeStatus:
    """Classification plus the ratios it was based on (for traceability)."""

    status: StockStatus
    b_ratio: float | None  # B / B_MSY   (or SSB / SSB_ref)
    f_ratio: float | None  # F / F_MSY
    reason: str


def classify_status(
    *,
    f_current: float | None,
    f_msy: float | None,
    biomass: float | None,
    b_msy: float | None,
    b_ratio_direct: float | None = None,
    f_ratio_direct: float | None = None,
) -> KobeStatus:
    """Classify one stock-year into a Kobe quadrant.

    Ratios may be supplied directly (``*_direct``) when a source already reports
    B/B_MSY or F/F_MSY; otherwise they are derived from the raw value and its
    reference point. Missing or non-positive reference points yield ``unknown``.
    """
    b_ratio = _ratio(biomass, b_msy, b_ratio_direct)
    f_ratio = _ratio(f_current, f_msy, f_ratio_direct)

    if b_ratio is None or f_ratio is None:
        missing = []
        if b_ratio is None:
            missing.append("biomass/B_MSY")
        if f_ratio is None:
            missing.append("F/F_MSY")
        return KobeStatus(
            status="unknown",
            b_ratio=b_ratio,
            f_ratio=f_ratio,
            reason=f"insufficient reference points: missing {', '.join(missing)}",
        )

    overfished = b_ratio < 1.0
    overfishing = f_ratio > 1.0

    if overfished and overfishing:
        status: StockStatus = "both"
        reason = "biomass below B_MSY and F above F_MSY"
    elif overfished:
        status = "overfished"
        reason = "biomass below B_MSY, F at/below F_MSY"
    elif overfishing:
        status = "overfishing"
        reason = "F above F_MSY, biomass at/above B_MSY"
    else:
        status = "healthy"
        reason = "biomass at/above B_MSY and F at/below F_MSY"

    return KobeStatus(status=status, b_ratio=b_ratio, f_ratio=f_ratio, reason=reason)


def _ratio(value: float | None, reference: float | None, direct: float | None) -> float | None:
    if direct is not None:
        return direct if direct >= 0 else None
    if value is None or reference is None or reference <= 0:
        return None
    return value / reference

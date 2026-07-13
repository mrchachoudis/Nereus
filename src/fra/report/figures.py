"""Matplotlib figure builders (DESIGN_PROMPT §10).

Pure rendering: each function takes Blackboard data and an output directory and
returns a :class:`FigureRef`. Axes are labelled with units and captions name the
source. Uses the non-interactive Agg backend so it runs headless in CI.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from fra.analysis import theil_sen  # noqa: E402
from fra.blackboard import Blackboard  # noqa: E402
from fra.models import AssessmentRecord, FigureRef  # noqa: E402


def build_all_figures(bb: Blackboard, out_dir: str | Path) -> list[FigureRef]:
    """Render every applicable figure for the run; skip those lacking data."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    figures: list[FigureRef] = []

    if len(bb.landings) >= 3:
        figures.append(
            _timeseries_with_trend(
                bb.landings,
                out,
                fid="fig-landings",
                ylabel="Landings (tonnes)",
                title="Landings",
                value=lambda r: r.tonnes,
                source="landings connectors",
            )
        )
    ssb = [r for r in bb.assessments if r.ssb is not None]
    if len(ssb) >= 3:
        figures.append(
            _timeseries_with_trend(
                ssb,
                out,
                fid="fig-ssb",
                ylabel="SSB",
                title="Spawning stock biomass",
                value=lambda r: r.ssb,
                source="assessment connectors",
            )
        )
    kobe = _kobe_plot(bb.assessments, out)
    if kobe is not None:
        figures.append(kobe)
    if bb.covariates and (bb.landings or ssb):
        figures.append(_covariate_overlay(bb, out))
    return figures


def _timeseries_with_trend(
    records: list[Any],
    out: Path,
    *,
    fid: str,
    ylabel: str,
    title: str,
    value: Callable[[Any], float],
    source: str,
) -> FigureRef:
    recs = sorted(records, key=lambda r: r.year)
    years = [r.year for r in recs]
    vals = [float(value(r)) for r in recs]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(years, vals, "o-", color="#1f6f8b", label=ylabel)
    if len(vals) >= 3:
        slope, intercept, (lo, hi) = theil_sen(vals)
        x0 = years[0]
        trend = [intercept + slope * (y - x0) for y in years]
        lo_line = [intercept + lo * (y - x0) for y in years]
        hi_line = [intercept + hi * (y - x0) for y in years]
        ax.plot(years, trend, "--", color="#c0392b", label=f"Theil-Sen slope {slope:.1f}/yr")
        ax.fill_between(years, lo_line, hi_line, color="#c0392b", alpha=0.12, label="95% slope CI")
    ax.set_xlabel("Year")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    path = out / f"{fid}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return FigureRef(
        id=fid,
        title=title,
        path=str(path.name),
        caption=f"{title} over time with Theil-Sen trend and 95% CI. Source: {source}.",
        kind="timeseries",
    )


def _kobe_plot(assessments: list[AssessmentRecord], out: Path) -> FigureRef | None:
    from fra.analysis import classify_status

    points: list[tuple[float, float, int]] = []
    for r in sorted(assessments, key=lambda r: r.year):
        k = classify_status(f_current=r.f_current, f_msy=r.f_msy, biomass=r.ssb, b_msy=r.b_msy)
        if k.b_ratio is not None and k.f_ratio is not None:
            points.append((k.b_ratio, k.f_ratio, r.year))
    if not points:
        return None

    fig, ax = plt.subplots(figsize=(6, 5))
    # quadrant shading
    ax.axhspan(0, 1, xmin=0.0, xmax=0.5, facecolor="#f5b7b1", alpha=0.4)  # overfished
    ax.axhspan(1, ax.get_ylim()[1] or 3, xmin=0.5, facecolor="#f9e79f", alpha=0.0)
    ax.axvline(1.0, color="gray", lw=1)
    ax.axhline(1.0, color="gray", lw=1)
    b = [p[0] for p in points]
    f = [p[1] for p in points]
    years = [p[2] for p in points]
    sc = ax.scatter(b, f, c=years, cmap="viridis", s=40, zorder=3)
    ax.plot(b, f, "-", color="gray", alpha=0.5, zorder=2)
    fig.colorbar(sc, ax=ax, label="Year")
    ax.set_xlabel("B / B_MSY")
    ax.set_ylabel("F / F_MSY")
    ax.set_title("Kobe plot — stock status trajectory")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    path = out / "fig-kobe.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return FigureRef(
        id="fig-kobe",
        title="Kobe plot",
        path=str(path.name),
        caption="Stock-status trajectory in Kobe space (B/B_MSY vs F/F_MSY); "
        "lower-right is healthy. Source: assessment connectors.",
        kind="kobe",
    )


def _covariate_overlay(bb: Blackboard, out: Path) -> FigureRef:
    ssb = [r for r in bb.assessments if r.ssb is not None]
    metric_recs: list[Any] = list(ssb) if ssb else list(bb.landings)
    metric_label = "SSB" if ssb else "Landings (tonnes)"
    recs = sorted(metric_recs, key=lambda r: r.year)
    m_years = [r.year for r in recs]
    m_vals = [float(r.ssb) if ssb else float(r.tonnes) for r in recs]

    cov = bb.covariates[0]
    good = [tp for tp in cov.good_values() if tp.value is not None]
    cov_years = [tp.date.year for tp in good]
    cov_vals = [float(tp.value) for tp in good if tp.value is not None]

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax1.plot(m_years, m_vals, "o-", color="#1f6f8b", label=metric_label)
    ax1.set_xlabel("Year")
    ax1.set_ylabel(metric_label, color="#1f6f8b")
    ax2 = ax1.twinx()
    ax2.plot(cov_years, cov_vals, "s--", color="#c0392b", label=cov.variable.upper())
    ax2.set_ylabel(f"{cov.variable.upper()} ({cov.unit})", color="#c0392b")
    ax1.set_title(f"{metric_label} vs {cov.variable.upper()}")
    ax1.grid(alpha=0.3)
    path = out / "fig-covariate-overlay.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return FigureRef(
        id="fig-covariate-overlay",
        title=f"{metric_label} vs {cov.variable.upper()}",
        path=str(path.name),
        caption=f"{metric_label} overlaid with {cov.variable.upper()} ({cov.unit}). "
        "Association is not causal. Source: assessment + ocean connectors.",
        kind="covariate_overlay",
    )

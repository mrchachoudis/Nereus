"""Deterministic quantitative layer.

Every function here is pure (no I/O, no LLM, no global state) and unit-tested on
synthetic data with known properties. The agents wrap these outputs into
:class:`~fra.models.AnalysisResult` objects; the math lives here.
"""

from __future__ import annotations

from fra.analysis.association import LagProfile, lagged_association
from fra.analysis.status import KobeStatus, classify_status
from fra.analysis.trends import TrendResult, mann_kendall, seasonal_mann_kendall, theil_sen

__all__ = [
    "TrendResult",
    "mann_kendall",
    "seasonal_mann_kendall",
    "theil_sen",
    "KobeStatus",
    "classify_status",
    "LagProfile",
    "lagged_association",
]

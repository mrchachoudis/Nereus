"""Outputs of the deterministic analysis layer."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from fra.models.common import FraModel

AnalysisKind = Literal["trend", "status_classification", "association"]


class AnalysisResult(FraModel):
    """One quantitative finding.

    The grounding contract requires effect sizes and uncertainty, never a bare
    point estimate: ``statistic`` is always accompanied by whatever of
    ``p_value``/``confidence_interval``/``effect_size`` the method produces, and
    ``inputs`` lists the record IDs the result depends on so Synthesis can cite
    them and the Critic can verify them.
    """

    id: str
    kind: AnalysisKind
    target: str
    statistic: float
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_interval: tuple[float, float] | None = None
    effect_size: float | None = None
    interpretation: str
    inputs: list[str] = Field(default_factory=list)
    # Free-form structured detail (e.g. full lag profile, slope units, method).
    detail: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ci_ordered(self) -> AnalysisResult:
        if self.confidence_interval is not None:
            lo, hi = self.confidence_interval
            if lo > hi:
                raise ValueError(f"confidence_interval lower ({lo}) exceeds upper ({hi})")
        return self

"""The research plan produced by the Planner agent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from fra.models.common import FraModel, SpatialUnit, Taxon

DataDomain = Literal["landings", "assessment", "ocean", "literature"]


class TimeRange(FraModel):
    """Inclusive year range for the analysis."""

    start_year: int = Field(ge=1800, le=2100)
    end_year: int = Field(ge=1800, le=2100)

    @model_validator(mode="after")
    def _ordered(self) -> TimeRange:
        if self.end_year < self.start_year:
            raise ValueError(f"end_year ({self.end_year}) precedes start_year ({self.start_year})")
        return self

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.end_year + 1))


class SubQuestion(FraModel):
    """A decomposed, individually-answerable component of the research question."""

    id: str
    text: str
    domains: list[DataDomain] = Field(
        default_factory=list,
        description="Which data domains must be retrieved to answer this.",
    )


class ResearchPlan(FraModel):
    """Structured decomposition of a user's research question.

    The Planner emits this. If the question is under-specified (no resolvable
    species, area, or time range) the Planner sets ``needs_clarification`` and
    populates ``clarification_questions`` instead of a usable plan.
    """

    question: str
    taxa: list[Taxon] = Field(default_factory=list)
    areas: list[SpatialUnit] = Field(default_factory=list)
    time_range: TimeRange | None = None
    required_domains: list[DataDomain] = Field(default_factory=list)
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    rationale: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> ResearchPlan:
        if self.needs_clarification:
            if not self.clarification_questions:
                raise ValueError(
                    "needs_clarification is True but no clarification_questions provided"
                )
        else:
            missing = [
                name
                for name, val in (
                    ("taxa", self.taxa),
                    ("areas", self.areas),
                    ("required_domains", self.required_domains),
                )
                if not val
            ]
            if missing:
                raise ValueError("an approved plan must specify " + ", ".join(missing))
            if self.time_range is None:
                raise ValueError("an approved plan must specify a time_range")
        return self

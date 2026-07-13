"""Primary-literature reference model."""

from __future__ import annotations

from pydantic import Field

from fra.models.common import FraModel


class Reference(FraModel):
    """A ranked primary-literature item and its relation to the analyses.

    ``supports`` / ``contradicts`` hold :class:`~fra.models.analysis.AnalysisResult`
    IDs, letting the Synthesis agent flag where the literature agrees or
    disagrees with the quantitative findings.
    """

    id: str
    doi: str | None = None
    title: str
    year: int = Field(ge=1500, le=2100)
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    relevance_score: float = Field(ge=0.0, le=1.0)
    supports: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    source: str = "unknown"
    url: str | None = None

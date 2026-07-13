"""Report-composition models: draft, critic notes, and the final report."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from fra.models.common import FraModel, utcnow
from fra.models.plan import ResearchPlan


class Citation(FraModel):
    """A resolvable pointer to a grounding source.

    ``ref_id`` is the ID of a record on the Blackboard: a ``source_ref`` of a
    data record, an ``AnalysisResult.id``, or a ``Reference.id``. The Critic
    mechanically checks that each ``ref_id`` actually exists.
    """

    marker: str = Field(description='Inline marker used in prose, e.g. "[L1]".')
    ref_id: str
    kind: Literal["landings", "assessment", "covariate", "analysis", "reference"]
    detail: str | None = None


class CitedClaim(FraModel):
    """A single sentence/claim carrying zero or more citation markers."""

    text: str
    citation_markers: list[str] = Field(default_factory=list)
    # True for sentences that make a quantitative assertion; these MUST be cited.
    is_quantitative: bool = False


class ReportSection(FraModel):
    """A titled block of cited claims (e.g. Methods, Results, Limitations)."""

    title: str
    claims: list[CitedClaim] = Field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [f"## {self.title}", ""]
        for claim in self.claims:
            marks = "".join(claim.citation_markers)
            lines.append(f"{claim.text} {marks}".rstrip())
        lines.append("")
        return "\n".join(lines)


class FigureRef(FraModel):
    """A pointer to a rendered figure file plus its caption."""

    id: str
    title: str
    path: str
    caption: str
    kind: Literal["timeseries", "kobe", "covariate_overlay", "other"] = "other"


class DraftReport(FraModel):
    """Synthesis output, pre-approval."""

    run_id: str
    question: str
    sections: list[ReportSection] = Field(default_factory=list)
    figures: list[FigureRef] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    revision: int = 0

    def all_claim_markers(self) -> list[str]:
        return [m for s in self.sections for c in s.claims for m in c.citation_markers]


class CriticNote(FraModel):
    """One adversarial finding from the Critic."""

    severity: Literal["blocker", "major", "minor"]
    category: Literal[
        "unsupported_claim",
        "unit_inconsistency",
        "overconfident_language",
        "missing_uncertainty",
        "dangling_citation",
        "other",
    ]
    section_title: str | None = None
    claim_text: str | None = None
    message: str


class CriticVerdict(FraModel):
    """The Critic's pass/revise decision plus its notes."""

    verdict: Literal["pass", "revise"]
    notes: list[CriticNote] = Field(default_factory=list)
    round: int = 0

    @property
    def blocking(self) -> bool:
        return self.verdict == "revise" and any(
            n.severity in {"blocker", "major"} for n in self.notes
        )


class FinalReport(FraModel):
    """The approved (or capped) report, rendered to markdown + JSON sidecar."""

    run_id: str
    question: str
    plan: ResearchPlan
    sections: list[ReportSection] = Field(default_factory=list)
    figures: list[FigureRef] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    outstanding_critic_notes: list[CriticNote] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)

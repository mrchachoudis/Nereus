"""The shared Blackboard: single source of truth for one run (DESIGN_PROMPT §4).

Agents read inputs from the Blackboard and write outputs back to it. Every write
of external data must go through :meth:`Blackboard.record`, which appends a
:class:`ProvenanceEntry` - provenance is mandatory, not optional.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from fra.models import (
    AnalysisResult,
    AssessmentRecord,
    CovariateSeries,
    CriticNote,
    DraftReport,
    FigureRef,
    FinalReport,
    FraModel,
    LandingsRecord,
    ProvenanceEntry,
    Reference,
    ResearchPlan,
)


class CoverageGap(FraModel):
    """A recorded absence of data in a domain - gaps are data, never fabricated."""

    domain: str
    detail: str
    connector: str | None = None


class Blackboard(FraModel):
    """Mutable per-run state container."""

    run_id: str
    question: str | None = None
    plan: ResearchPlan | None = None
    landings: list[LandingsRecord] = Field(default_factory=list)
    assessments: list[AssessmentRecord] = Field(default_factory=list)
    covariates: list[CovariateSeries] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    analyses: list[AnalysisResult] = Field(default_factory=list)
    draft: DraftReport | None = None
    figures: list[FigureRef] = Field(default_factory=list)
    critic_notes: list[CriticNote] = Field(default_factory=list)
    final: FinalReport | None = None
    provenance: list[ProvenanceEntry] = Field(default_factory=list)
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)

    # -- provenance-aware writers --------------------------------------------

    def record(
        self,
        *,
        source: str,
        url_or_query: str,
        records: Sequence[BaseModel],
        note: str | None = None,
    ) -> None:
        """Append typed records to the right bucket and log provenance.

        Accepts any :class:`BaseModel` sequence for connector ergonomics but
        routes strictly by concrete type; an unroutable type fails loud. Empty
        ``records`` still appends a provenance entry (documenting that the source
        was queried and returned nothing) but writes no data.
        """
        ids: list[str] = []
        for rec in records:
            if isinstance(rec, LandingsRecord):
                self.landings.append(rec)
                ids.append(rec.id)
            elif isinstance(rec, AssessmentRecord):
                self.assessments.append(rec)
                ids.append(rec.id)
            elif isinstance(rec, CovariateSeries):
                self.covariates.append(rec)
                ids.append(rec.id)
            elif isinstance(rec, Reference):
                self.references.append(rec)
                ids.append(rec.id)
            else:
                raise TypeError(f"unroutable record type: {type(rec)!r}")

        self.provenance.append(
            ProvenanceEntry(
                source=source,
                url_or_query=url_or_query,
                record_ids=ids,
                note=note,
            )
        )

    def add_analyses(self, results: Sequence[AnalysisResult]) -> None:
        self.analyses.extend(results)

    def add_gap(self, domain: str, detail: str, connector: str | None = None) -> None:
        self.coverage_gaps.append(CoverageGap(domain=domain, detail=detail, connector=connector))

    # -- lookups used by the grounding contract ------------------------------

    def known_ids(self) -> set[str]:
        """Every citable ID on the board (record source_refs, analysis + ref IDs).

        The Critic checks each citation's ``ref_id`` against this set; a citation
        to an ID not present here is an automatic revise.
        """
        ids: set[str] = set()
        data_records: list[LandingsRecord | AssessmentRecord | CovariateSeries] = [
            *self.landings,
            *self.assessments,
            *self.covariates,
        ]
        for rec in data_records:
            ids.add(rec.id)
            ids.add(rec.source_ref)
        ids.update(a.id for a in self.analyses)
        ids.update(r.id for r in self.references)
        return ids

    def has_retrieval_data(self) -> bool:
        return bool(self.landings or self.assessments or self.covariates)

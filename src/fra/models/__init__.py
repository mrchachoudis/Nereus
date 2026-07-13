"""All pydantic data contracts for the system (DESIGN_PROMPT §5).

Import models from this package rather than the submodules so call sites stay
stable if the internal file layout changes.
"""

from __future__ import annotations

from fra.models.analysis import AnalysisKind, AnalysisResult
from fra.models.common import (
    FraModel,
    ProvenanceEntry,
    SpatialUnit,
    Taxon,
    TimePoint,
    utcnow,
)
from fra.models.literature import Reference
from fra.models.plan import (
    DataDomain,
    ResearchPlan,
    SubQuestion,
    TimeRange,
)
from fra.models.records import (
    AssessmentRecord,
    CovariateSeries,
    CovariateVariable,
    LandingsRecord,
    StockStatus,
)
from fra.models.report import (
    Citation,
    CitedClaim,
    CriticNote,
    CriticVerdict,
    DraftReport,
    FigureRef,
    FinalReport,
    ReportSection,
)

__all__ = [
    # common
    "FraModel",
    "ProvenanceEntry",
    "SpatialUnit",
    "Taxon",
    "TimePoint",
    "utcnow",
    # plan
    "DataDomain",
    "ResearchPlan",
    "SubQuestion",
    "TimeRange",
    # records
    "AssessmentRecord",
    "CovariateSeries",
    "CovariateVariable",
    "LandingsRecord",
    "StockStatus",
    # analysis
    "AnalysisKind",
    "AnalysisResult",
    # literature
    "Reference",
    # report
    "Citation",
    "CitedClaim",
    "CriticNote",
    "CriticVerdict",
    "DraftReport",
    "FigureRef",
    "FinalReport",
    "ReportSection",
]

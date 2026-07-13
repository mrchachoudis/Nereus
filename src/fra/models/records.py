"""Ingested data records: landings, assessments, and ocean covariates.

All external API JSON is coerced into these models at the connector boundary;
no downstream code touches raw source payloads.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from fra.models.common import FraModel, SpatialUnit, Taxon, TimePoint

StockStatus = Literal["healthy", "overfished", "overfishing", "both", "unknown"]

CovariateVariable = Literal[
    "sst",
    "chlor_a",
    "salinity",
    "upwelling_index",
    "sea_level_anomaly",
    "oxygen",
]


class LandingsRecord(FraModel):
    """A single (taxon, area, year) landings/catch observation in tonnes."""

    id: str
    taxon: Taxon
    area: SpatialUnit
    year: int = Field(ge=1800, le=2100)
    tonnes: float = Field(ge=0.0)
    gear: str | None = None
    source: str
    source_ref: str


class AssessmentRecord(FraModel):
    """A stock-assessment output for a (taxon, area, year).

    Reference points (``f_msy``, ``b_msy``) and the ``status`` label are often
    absent in source data; they remain ``None`` and the status classifier returns
    ``"unknown"`` rather than guessing.
    """

    id: str
    taxon: Taxon
    area: SpatialUnit
    year: int = Field(ge=1800, le=2100)
    ssb: float | None = Field(default=None, ge=0.0, description="Spawning stock biomass.")
    f_current: float | None = Field(default=None, ge=0.0, description="Fishing mortality.")
    f_msy: float | None = Field(default=None, ge=0.0)
    b_msy: float | None = Field(default=None, ge=0.0)
    status: StockStatus | None = None
    source: str
    source_ref: str


class CovariateSeries(FraModel):
    """A time series of one environmental variable over one spatial unit."""

    id: str
    variable: CovariateVariable
    area: SpatialUnit
    values: list[TimePoint] = Field(default_factory=list)
    unit: str
    source: str
    source_ref: str

    @model_validator(mode="after")
    def _sorted_unique_dates(self) -> CovariateSeries:
        dates = [tp.date for tp in self.values]
        if len(dates) != len(set(dates)):
            raise ValueError(f"CovariateSeries {self.id} has duplicate dates")
        if dates != sorted(dates):
            # Normalize rather than reject: chronological order is an invariant
            # the analysis layer relies on.
            self.values = sorted(self.values, key=lambda tp: tp.date)
        return self

    def good_values(self) -> list[TimePoint]:
        """Points with a non-missing value and a non-bad QC flag (<= 2 or unset)."""
        return [
            tp
            for tp in self.values
            if tp.value is not None and (tp.qc_flag is None or tp.qc_flag <= 2)
        ]

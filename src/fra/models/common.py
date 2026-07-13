"""Shared, cross-cutting data models.

These are the low-level building blocks (taxonomy, space, time, provenance)
reused by the record models in :mod:`fra.models.records` and everything above.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

# FAO major fishing area codes look like "37", "37.2", or "37.2.1".
_FAO_AREA_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){0,2}$")


def utcnow() -> datetime:
    """Timezone-aware UTC now. Centralized so tests can monkeypatch it."""
    return datetime.now(timezone.utc)


class FraModel(BaseModel):
    """Base for all project models.

    ``extra="forbid"`` makes ingestion fail loud: if a connector maps an unknown
    field into a model, we want an error at the boundary, not silent data loss.
    """

    model_config = ConfigDict(extra="forbid", frozen=False, validate_assignment=True)


class Taxon(FraModel):
    """A species (or higher taxon) resolved, where possible, to a WoRMS AphiaID."""

    scientific_name: str = Field(min_length=1)
    aphia_id: int | None = None
    common_name: str | None = None

    @field_validator("scientific_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


class SpatialUnit(FraModel):
    """A spatial analysis unit, normalized to FAO major fishing areas.

    ``gsa`` (GFCM Geographical Sub-Area) is populated only for Mediterranean/
    Black Sea areas (FAO 37) where the finer GSA grid applies.
    """

    fao_area: str = Field(description='FAO major fishing area, e.g. "37.2.1".')
    gsa: str | None = None
    label: str

    @field_validator("fao_area")
    @classmethod
    def _validate_fao(cls, v: str) -> str:
        v = v.strip()
        if not _FAO_AREA_RE.match(v):
            raise ValueError(
                f"fao_area {v!r} is not a valid FAO major-area code "
                '(expected e.g. "37", "37.2", or "37.2.1")'
            )
        return v


class TimePoint(FraModel):
    """A single observation in a time series with an optional QC flag.

    ``qc_flag`` follows the common IODE/Argo convention: 1 = good, 2 = probably
    good, 3 = probably bad, 4 = bad, 9 = missing. ``None`` means unassessed.
    """

    date: date
    value: float | None
    qc_flag: int | None = None

    @field_validator("qc_flag")
    @classmethod
    def _validate_qc(cls, v: int | None) -> int | None:
        if v is not None and v not in {1, 2, 3, 4, 5, 6, 7, 8, 9}:
            raise ValueError(f"qc_flag {v} outside the IODE 1-9 range")
        return v


class ProvenanceEntry(FraModel):
    """One append-only audit-log entry recording where records came from.

    Every write of external data to the Blackboard must append one of these so a
    reviewer can reproduce every number in the final report.
    """

    source: str
    url_or_query: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    record_ids: list[str] = Field(default_factory=list)
    note: str | None = None

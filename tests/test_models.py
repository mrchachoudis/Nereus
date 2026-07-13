"""Tests for the pydantic data contracts (DESIGN_PROMPT §5)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from fra.models import (
    CovariateSeries,
    LandingsRecord,
    ResearchPlan,
    SpatialUnit,
    Taxon,
    TimePoint,
    TimeRange,
)


def test_spatial_unit_rejects_bad_fao_code() -> None:
    with pytest.raises(ValidationError):
        SpatialUnit(fao_area="Mediterranean", label="bad")


def test_spatial_unit_accepts_nested_code() -> None:
    su = SpatialUnit(fao_area="37.2.1", label="ok")
    assert su.fao_area == "37.2.1"


def test_landings_rejects_negative_tonnes(hake: Taxon, area_37_2: SpatialUnit) -> None:
    with pytest.raises(ValidationError):
        LandingsRecord(
            id="x",
            taxon=hake,
            area=area_37_2,
            year=2015,
            tonnes=-1.0,
            source="s",
            source_ref="r",
        )


def test_extra_fields_forbidden(hake: Taxon, area_37_2: SpatialUnit) -> None:
    with pytest.raises(ValidationError):
        LandingsRecord(
            id="x",
            taxon=hake,
            area=area_37_2,
            year=2015,
            tonnes=1.0,
            source="s",
            source_ref="r",
            bogus_field=123,  # type: ignore[call-arg]
        )


def test_timerange_ordering() -> None:
    with pytest.raises(ValidationError):
        TimeRange(start_year=2020, end_year=2010)
    assert TimeRange(start_year=2010, end_year=2012).years == [2010, 2011, 2012]


def test_plan_requires_specifics_when_approved(hake: Taxon, area_37_2: SpatialUnit) -> None:
    with pytest.raises(ValidationError):
        ResearchPlan(question="vague", needs_clarification=False)
    ok = ResearchPlan(
        question="ok",
        taxa=[hake],
        areas=[area_37_2],
        time_range=TimeRange(start_year=2010, end_year=2020),
        required_domains=["landings"],
    )
    assert not ok.needs_clarification


def test_plan_clarification_needs_questions() -> None:
    with pytest.raises(ValidationError):
        ResearchPlan(question="vague", needs_clarification=True)
    p = ResearchPlan(
        question="vague",
        needs_clarification=True,
        clarification_questions=["Which species?"],
    )
    assert p.clarification_questions


def test_covariate_dedups_and_sorts(area_37_2: SpatialUnit) -> None:
    unsorted = [
        TimePoint(date=date(2012, 1, 1), value=2.0),
        TimePoint(date=date(2010, 1, 1), value=1.0),
    ]
    cov = CovariateSeries(
        id="c",
        variable="sst",
        area=area_37_2,
        values=unsorted,
        unit="degree_C",
        source="s",
        source_ref="r",
    )
    assert [tp.date.year for tp in cov.values] == [2010, 2012]


def test_covariate_rejects_duplicate_dates(area_37_2: SpatialUnit) -> None:
    dup = [
        TimePoint(date=date(2010, 1, 1), value=1.0),
        TimePoint(date=date(2010, 1, 1), value=2.0),
    ]
    with pytest.raises(ValidationError):
        CovariateSeries(
            id="c",
            variable="sst",
            area=area_37_2,
            values=dup,
            unit="degree_C",
            source="s",
            source_ref="r",
        )


def test_covariate_good_values_filters_qc(area_37_2: SpatialUnit) -> None:
    values = [
        TimePoint(date=date(2010, 1, 1), value=1.0, qc_flag=1),
        TimePoint(date=date(2011, 1, 1), value=99.0, qc_flag=4),  # bad
        TimePoint(date=date(2012, 1, 1), value=None, qc_flag=9),  # missing
    ]
    cov = CovariateSeries(
        id="c",
        variable="sst",
        area=area_37_2,
        values=values,
        unit="degree_C",
        source="s",
        source_ref="r",
    )
    good = cov.good_values()
    assert len(good) == 1 and good[0].value == 1.0


def test_taxon_strips_name() -> None:
    assert Taxon(scientific_name="  Gadus morhua  ").scientific_name == "Gadus morhua"

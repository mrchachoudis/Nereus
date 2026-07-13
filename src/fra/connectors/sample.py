"""Offline, deterministic sample connectors.

These fabricate *nothing at runtime* from the network — they return a fixed,
clearly-labeled synthetic dataset for European hake in FAO 37.2 so the example
run, the end-to-end test, and a keyless quickstart all work offline. Every record
is stamped with ``source="sample_*"`` and a ``source_ref`` that makes its
synthetic origin explicit, so it can never be mistaken for real data in a report.

Do not enable these in a production config; they exist for demos and tests.
"""

from __future__ import annotations

import math
from datetime import date

from fra.models import (
    AssessmentRecord,
    CovariateSeries,
    LandingsRecord,
    Reference,
    ResearchPlan,
    SpatialUnit,
    Taxon,
    TimePoint,
)

_HAKE = Taxon(scientific_name="Merluccius merluccius", aphia_id=126484, common_name="European hake")


def _jitter(i: int, amp: float, phase: float = 0.0) -> float:
    """Deterministic pseudo-noise so synthetic series aren't perfectly collinear.

    Keeps the sample data reproducible (no RNG) while making correlations
    realistic (|r| < 1) and p-values non-degenerate.
    """
    return amp * math.sin(1.7 * i + phase)


def _area(plan: ResearchPlan) -> SpatialUnit:
    return plan.areas[0] if plan.areas else SpatialUnit(fao_area="37.2", label="Adriatic/Ionian")


def _years(plan: ResearchPlan) -> list[int]:
    if plan.time_range is None:
        return list(range(2010, 2021))
    return plan.time_range.years


class SampleLandingsConnector:
    name = "sample_landings"
    domain = "landings"

    async def fetch(self, plan: ResearchPlan) -> list[LandingsRecord]:
        area = _area(plan)
        years = _years(plan)
        base = 12500.0
        out: list[LandingsRecord] = []
        for i, year in enumerate(years):
            # Gently declining catch, characteristic of a pressured stock.
            tonnes = round(base - i * 480 + _jitter(i, 220, 0.4), 1)
            out.append(
                LandingsRecord(
                    id=f"sample_landings-{year}",
                    taxon=_HAKE,
                    area=area,
                    year=year,
                    tonnes=max(tonnes, 0.0),
                    gear="bottom trawl",
                    source=self.name,
                    source_ref=f"SAMPLE:landings:{area.fao_area}:hake:{year}",
                )
            )
        return out


class SampleAssessmentConnector:
    name = "sample_assessment"
    domain = "assessment"

    async def fetch(self, plan: ResearchPlan) -> list[AssessmentRecord]:
        area = _area(plan)
        years = _years(plan)
        out: list[AssessmentRecord] = []
        for i, year in enumerate(years):
            out.append(
                AssessmentRecord(
                    id=f"sample_assessment-{year}",
                    taxon=_HAKE,
                    area=area,
                    year=year,
                    ssb=round(46000 - i * 900 + _jitter(i, 700, 1.1), 1),
                    f_current=round(0.62 - i * 0.005, 3),
                    f_msy=0.25,
                    b_msy=80000.0,
                    status=None,  # let the classifier decide from ratios
                    source=self.name,
                    source_ref=f"SAMPLE:assessment:{area.fao_area}:hake:{year}",
                )
            )
        return out


class SampleOceanConnector:
    name = "sample_ocean"
    domain = "ocean"

    async def fetch(self, plan: ResearchPlan) -> list[CovariateSeries]:
        area = _area(plan)
        years = _years(plan)
        sst = [
            TimePoint(
                date=date(y, 7, 1),
                value=round(18.4 + 0.11 * i + _jitter(i, 0.18, 2.0), 3),
                qc_flag=1,
            )
            for i, y in enumerate(years)
        ]
        chl = [
            TimePoint(
                date=date(y, 7, 1),
                value=round(0.42 - 0.006 * i + _jitter(i, 0.03, 0.7), 4),
                qc_flag=1,
            )
            for i, y in enumerate(years)
        ]
        return [
            CovariateSeries(
                id=f"sample_ocean-sst-{area.fao_area}",
                variable="sst",
                area=area,
                values=sst,
                unit="degree_C",
                source=self.name,
                source_ref=f"SAMPLE:ocean:sst:{area.fao_area}",
            ),
            CovariateSeries(
                id=f"sample_ocean-chl-{area.fao_area}",
                variable="chlor_a",
                area=area,
                values=chl,
                unit="mg m-3",
                source=self.name,
                source_ref=f"SAMPLE:ocean:chlor_a:{area.fao_area}",
            ),
        ]


class SampleLiteratureConnector:
    name = "sample_literature"
    domain = "literature"

    async def fetch(self, plan: ResearchPlan) -> list[Reference]:
        return [
            Reference(
                id="sample_lit-1",
                doi="10.0000/sample.hake.2019",
                title="Stock status and warming-linked distribution shifts of European hake in the central Mediterranean",
                year=2019,
                authors=["A. Ricercatore", "B. Investigadora"],
                abstract="A synthetic reference (sample data) reporting sustained overfishing "
                "of Merluccius merluccius and a positive association between sea surface "
                "temperature and juvenile distribution.",
                relevance_score=0.95,
                source=self.name,
                url="https://doi.org/10.0000/sample.hake.2019",
            ),
            Reference(
                id="sample_lit-2",
                doi="10.0000/sample.med.sst.2021",
                title="Mediterranean sea surface temperature trends 2000–2020 and implications for demersal fisheries",
                year=2021,
                authors=["C. Oceanographer"],
                abstract="A synthetic reference (sample data) documenting a Mediterranean SST "
                "warming trend and discussing associational links to demersal stock productivity.",
                relevance_score=0.88,
                source=self.name,
                url="https://doi.org/10.0000/sample.med.sst.2021",
            ),
        ]

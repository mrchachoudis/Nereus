"""Shared pytest fixtures.

Everything here is offline and deterministic. Connector tests use httpx
``MockTransport`` (see :func:`mock_http`) rather than live network or recorded
cassettes so CI needs no keys and no network. A ``cassettes/`` directory of
recorded JSON responses backs the mocks and documents real payload shapes.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from fra.blackboard import Blackboard
from fra.llm import LLMResult, Message, parse_agent_name
from fra.models import (
    AssessmentRecord,
    CovariateSeries,
    LandingsRecord,
    ResearchPlan,
    SpatialUnit,
    Taxon,
    TimePoint,
    TimeRange,
)

CASSETTE_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture
def hake() -> Taxon:
    return Taxon(
        scientific_name="Merluccius merluccius", aphia_id=126484, common_name="European hake"
    )


@pytest.fixture
def area_37_2() -> SpatialUnit:
    return SpatialUnit(fao_area="37.2", gsa=None, label="Mediterranean - Adriatic/Ionian")


@pytest.fixture
def plan(hake: Taxon, area_37_2: SpatialUnit) -> ResearchPlan:
    return ResearchPlan(
        question="Assess stock status and environmental drivers of European hake in FAO 37.2, 2010–2020.",
        taxa=[hake],
        areas=[area_37_2],
        time_range=TimeRange(start_year=2010, end_year=2020),
        required_domains=["landings", "assessment", "ocean", "literature"],
    )


@pytest.fixture
def landings(hake: Taxon, area_37_2: SpatialUnit) -> list[LandingsRecord]:
    # Declining landings 2010–2020.
    base = 12000.0
    out: list[LandingsRecord] = []
    for i, year in enumerate(range(2010, 2021)):
        out.append(
            LandingsRecord(
                id=f"land-{year}",
                taxon=hake,
                area=area_37_2,
                year=year,
                tonnes=base - i * 600,
                gear="bottom trawl",
                source="fao_landings",
                source_ref=f"FAO:37.2:hake:{year}",
            )
        )
    return out


@pytest.fixture
def assessments(hake: Taxon, area_37_2: SpatialUnit) -> list[AssessmentRecord]:
    out: list[AssessmentRecord] = []
    for year in range(2010, 2021):
        out.append(
            AssessmentRecord(
                id=f"assess-{year}",
                taxon=hake,
                area=area_37_2,
                year=year,
                ssb=40000.0,
                f_current=0.6,
                f_msy=0.25,
                b_msy=80000.0,
                status=None,
                source="ram_legacy",
                source_ref=f"RAM:hake:37.2:{year}",
            )
        )
    return out


@pytest.fixture
def covariate(area_37_2: SpatialUnit) -> CovariateSeries:
    values = [
        TimePoint(date=date(y, 7, 1), value=18.0 + 0.15 * (y - 2010), qc_flag=1)
        for y in range(2010, 2021)
    ]
    return CovariateSeries(
        id="cov-sst",
        variable="sst",
        area=area_37_2,
        values=values,
        unit="degree_C",
        source="erddap_ocean",
        source_ref="ERDDAP:sst:37.2",
    )


@pytest.fixture
def blackboard(
    plan: ResearchPlan,
    landings: list[LandingsRecord],
    assessments: list[AssessmentRecord],
    covariate: CovariateSeries,
) -> Blackboard:
    bb = Blackboard(run_id="test-run", plan=plan)
    bb.record(source="fao_landings", url_or_query="test", records=landings)
    bb.record(source="ram_legacy", url_or_query="test", records=assessments)
    bb.record(source="erddap_ocean", url_or_query="test", records=[covariate])
    return bb


def load_cassette(name: str) -> dict[str, Any]:
    """Load a recorded JSON response body by cassette name."""
    return json.loads((CASSETTE_DIR / f"{name}.json").read_text(encoding="utf-8"))


class ScriptedBackend:
    """A mocked LLM backend returning canned JSON keyed by agent name (§13).

    Distinct from the offline DeterministicBackend: this returns fixed strings a
    test author supplies, so an agent's parsing/validation path can be exercised
    against a known payload (including deliberately malformed ones).
    """

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> LLMResult:
        agent = parse_agent_name(system) or "unknown"
        self.calls.append(agent)
        text = self._responses.get(agent, "{}")
        return LLMResult(text=text, input_tokens=10, output_tokens=10, model=model)


def mock_http(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    """Build an async client whose responses are keyed by URL-path substring."""

    def handler(request: httpx.Request) -> httpx.Response:
        for needle, response in routes.items():
            if needle in str(request.url):
                return response
        return httpx.Response(404, json={"error": "no mock route", "url": str(request.url)})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

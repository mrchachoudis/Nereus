"""Per-agent tests with mocked/offline LLM and sample connectors (DESIGN_PROMPT §13)."""

from __future__ import annotations

import json

import pytest

from fra.agents import (
    AnalysisAgent,
    CriticAgent,
    DataRetrievalAgent,
    LiteratureAgent,
    OceanographyAgent,
    PlannerAgent,
    SynthesisAgent,
    mechanical_check,
)
from fra.agents.base import AgentError
from fra.blackboard import Blackboard
from fra.connectors.sample import (
    SampleAssessmentConnector,
    SampleLandingsConnector,
    SampleLiteratureConnector,
    SampleOceanConnector,
)
from fra.llm import LLMClient
from fra.models import Citation, CitedClaim, DraftReport, ReportSection
from fra.offline import make_offline_llm
from tests.conftest import ScriptedBackend

# -- Planner -----------------------------------------------------------------


async def test_planner_offline_parses_question() -> None:
    llm = make_offline_llm()
    bb = Blackboard(run_id="r", question="Assess European hake in FAO 37.2, 2010-2020")
    bb = await PlannerAgent(llm).run(bb)
    assert bb.plan is not None
    assert not bb.plan.needs_clarification
    assert bb.plan.taxa[0].scientific_name == "Merluccius merluccius"
    assert bb.plan.areas[0].fao_area == "37.2"
    assert bb.plan.time_range.start_year == 2010


async def test_planner_requests_clarification_when_vague() -> None:
    llm = make_offline_llm()
    bb = Blackboard(run_id="r", question="Tell me about fish")
    bb = await PlannerAgent(llm).run(bb)
    assert bb.plan.needs_clarification
    assert bb.plan.clarification_questions


async def test_planner_with_scripted_backend() -> None:
    plan_json = json.dumps(
        {
            "question": "q",
            "taxa": [{"scientific_name": "Gadus morhua", "aphia_id": None, "common_name": None}],
            "areas": [{"fao_area": "27", "gsa": None, "label": "NE Atlantic"}],
            "time_range": {"start_year": 2000, "end_year": 2010},
            "required_domains": ["assessment"],
            "sub_questions": [],
            "needs_clarification": False,
            "clarification_questions": [],
            "rationale": "canned",
        }
    )
    backend = ScriptedBackend({"planner": plan_json})
    bb = Blackboard(run_id="r", question="q")
    bb = await PlannerAgent(LLMClient(backend)).run(bb)
    assert bb.plan.taxa[0].scientific_name == "Gadus morhua"
    assert backend.calls == ["planner"]


async def test_planner_retries_on_bad_json_then_fails() -> None:
    backend = ScriptedBackend({"planner": "not json at all"})
    bb = Blackboard(run_id="r", question="q")
    with pytest.raises(AgentError):  # AgentError wrapping LLMError
        await PlannerAgent(LLMClient(backend)).run(bb)
    assert backend.calls.count("planner") == 2  # structured() retries once


# -- Retrieval agents --------------------------------------------------------


async def test_data_retrieval_writes_records_and_provenance(plan) -> None:
    bb = Blackboard(run_id="r", plan=plan)
    conns = [SampleLandingsConnector(), SampleAssessmentConnector()]
    bb = await DataRetrievalAgent(conns).run(bb)
    assert len(bb.landings) == 11
    assert len(bb.assessments) == 11
    assert {p.source for p in bb.provenance} == {"sample_landings", "sample_assessment"}


async def test_retrieval_guard_blocks_without_plan() -> None:
    bb = Blackboard(run_id="r")  # no plan
    bb = await DataRetrievalAgent([SampleLandingsConnector()]).run(bb)
    assert bb.landings == []
    assert any(g.domain == "data_retrieval" for g in bb.coverage_gaps)


async def test_literature_dedups_by_doi(plan) -> None:
    bb = Blackboard(run_id="r", plan=plan)
    # two connectors returning overlapping DOIs
    bb = await LiteratureAgent([SampleLiteratureConnector(), SampleLiteratureConnector()]).run(bb)
    dois = [r.doi for r in bb.references]
    assert len(dois) == len(set(dois))  # no duplicate DOIs


async def test_ocean_agent_populates_covariates(plan) -> None:
    bb = Blackboard(run_id="r", plan=plan)
    bb = await OceanographyAgent([SampleOceanConnector()]).run(bb)
    assert {c.variable for c in bb.covariates} == {"sst", "chlor_a"}


# -- Analysis ----------------------------------------------------------------


async def test_analysis_produces_grounded_results(blackboard) -> None:
    bb = await AnalysisAgent().run(blackboard)
    kinds = {a.kind for a in bb.analyses}
    assert {"trend", "status_classification", "association"} <= kinds
    # every analysis references at least one real input id
    known = bb.known_ids()
    for a in bb.analyses:
        assert a.inputs
        assert all(i in known for i in a.inputs)


async def test_analysis_guard_gap_without_data(plan) -> None:
    bb = Blackboard(run_id="r", plan=plan)  # no retrieval data
    bb = await AnalysisAgent().run(bb)
    assert bb.analyses == []
    assert any(g.domain == "analysis" for g in bb.coverage_gaps)


# -- Synthesis ---------------------------------------------------------------


async def test_synthesis_offline_grounds_every_quantitative_claim(blackboard) -> None:
    llm = make_offline_llm()
    bb = await AnalysisAgent().run(blackboard)
    bb = await SynthesisAgent(llm).run(bb)
    draft = bb.draft
    assert draft is not None
    markers = {c.marker: c.ref_id for c in draft.citations}
    known = bb.known_ids()
    for section in draft.sections:
        for claim in section.claims:
            if claim.is_quantitative:
                assert claim.citation_markers, f"uncited quantitative claim: {claim.text}"
                for m in claim.citation_markers:
                    assert markers[m] in known


# -- Critic ------------------------------------------------------------------


def _draft_with(claim: CitedClaim, citations: list[Citation]) -> DraftReport:
    return DraftReport(
        run_id="r",
        question="q",
        sections=[ReportSection(title="Results", claims=[claim])],
        citations=citations,
    )


def test_mechanical_check_flags_uncited_quantitative() -> None:
    draft = _draft_with(
        CitedClaim(text="SSB fell 10%.", citation_markers=[], is_quantitative=True), []
    )
    notes = mechanical_check(draft, known_ids=set())
    assert any(n.category == "unsupported_claim" and n.severity == "blocker" for n in notes)


def test_mechanical_check_flags_dangling_citation() -> None:
    draft = _draft_with(
        CitedClaim(text="SSB fell.", citation_markers=["[A1]"], is_quantitative=True),
        [Citation(marker="[A1]", ref_id="does-not-exist", kind="analysis")],
    )
    notes = mechanical_check(draft, known_ids={"real-id"})
    assert any(n.category == "dangling_citation" and n.severity == "blocker" for n in notes)


def test_mechanical_check_passes_when_grounded() -> None:
    draft = _draft_with(
        CitedClaim(text="SSB fell.", citation_markers=["[A1]"], is_quantitative=True),
        [Citation(marker="[A1]", ref_id="real-id", kind="analysis")],
    )
    assert mechanical_check(draft, known_ids={"real-id"}) == []


async def test_critic_revises_dangling_then_passes_clean(blackboard) -> None:
    llm = make_offline_llm()
    bb = await AnalysisAgent().run(blackboard)

    # Inject a bad draft: a dangling citation must force a revise verdict.
    bb.draft = _draft_with(
        CitedClaim(text="SSB fell.", citation_markers=["[X]"], is_quantitative=True),
        [Citation(marker="[X]", ref_id="ghost-id", kind="analysis")],
    )
    critic = CriticAgent(llm)
    await critic.run(bb)
    assert critic.last_verdict.verdict == "revise"
    assert critic.last_verdict.blocking

    # Now a properly grounded draft passes.
    bb = await SynthesisAgent(llm).run(bb)
    await critic.run(bb)
    assert critic.last_verdict.verdict == "pass"

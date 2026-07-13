"""End-to-end orchestrator tests (DESIGN_PROMPT §13).

Runs a full fixture question through the whole graph with the offline LLM and
sample connectors, and asserts a well-formed FinalReport with non-empty citations
and a populated provenance log.
"""

from __future__ import annotations

import json

from fra.config import Settings, load_settings
from fra.connectors import build_connectors
from fra.connectors.sample import (
    SampleAssessmentConnector,
    SampleLandingsConnector,
    SampleLiteratureConnector,
    SampleOceanConnector,
)
from fra.offline import make_offline_llm
from fra.orchestrator import Orchestrator, Phase

QUESTION = "Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2020"


def _sample_connectors() -> list:
    return [
        SampleLandingsConnector(),
        SampleAssessmentConnector(),
        SampleOceanConnector(),
        SampleLiteratureConnector(),
    ]


async def test_full_run_produces_grounded_report(tmp_path) -> None:
    settings = Settings()
    orch = Orchestrator(settings, make_offline_llm(), _sample_connectors(), out_root=tmp_path)
    result = await orch.run(QUESTION, run_id="e2e")

    assert result.phase == Phase.DONE
    bb = result.blackboard
    assert bb.final is not None
    final = bb.final

    # non-empty citations, each pointing at a real Blackboard id
    assert final.citations
    known = bb.known_ids()
    assert all(c.ref_id in known for c in final.citations)

    # every quantitative claim is cited
    declared = {c.marker for c in final.citations}
    for section in final.sections:
        for claim in section.claims:
            if claim.is_quantitative:
                assert claim.citation_markers
                assert set(claim.citation_markers) <= declared

    # provenance populated for every source
    assert {p.source for p in bb.provenance} == {
        "sample_landings",
        "sample_assessment",
        "sample_ocean",
        "sample_literature",
    }

    # analyses cover all three kinds
    assert {a.kind for a in bb.analyses} >= {"trend", "status_classification", "association"}


async def test_run_writes_all_artifacts(tmp_path) -> None:
    settings = Settings()
    orch = Orchestrator(settings, make_offline_llm(), _sample_connectors(), out_root=tmp_path)
    result = await orch.run(QUESTION, run_id="artifacts")
    out = result.out_dir
    assert (out / "report.md").exists()
    assert (out / "report.json").exists()
    assert (out / "run_log.jsonl").exists()
    assert (out / "figures").is_dir()
    assert list((out / "figures").glob("*.png"))

    # sidecar is valid JSON with provenance
    sidecar = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert sidecar["provenance"]
    assert sidecar["final_report"]["citations"]

    # run log has phase transitions and token usage
    lines = [json.loads(line) for line in (out / "run_log.jsonl").read_text().splitlines()]
    events = {entry["event"] for entry in lines}
    assert "phase" in events and "done" in events and "token_usage" in events


async def test_clarification_short_circuits(tmp_path) -> None:
    settings = Settings()
    orch = Orchestrator(settings, make_offline_llm(), _sample_connectors(), out_root=tmp_path)
    result = await orch.run("Tell me about the ocean", run_id="vague")
    assert result.phase == Phase.CLARIFICATION_NEEDED
    assert result.clarification_questions
    assert result.blackboard.final is None


async def test_connector_failure_degrades_to_gap(tmp_path) -> None:
    """A connector that raises must not kill the run; its domain becomes a gap."""

    class BrokenOcean:
        name = "broken_ocean"
        domain = "ocean"

        async def fetch(self, plan):  # noqa: ANN001
            raise RuntimeError("simulated source outage")

    settings = Settings()
    conns = [
        SampleLandingsConnector(),
        SampleAssessmentConnector(),
        BrokenOcean(),
        SampleLiteratureConnector(),
    ]
    orch = Orchestrator(settings, make_offline_llm(), conns, out_root=tmp_path)
    result = await orch.run(QUESTION, run_id="degrade")

    assert result.phase == Phase.DONE  # still completes
    gaps = result.blackboard.coverage_gaps
    assert any(g.connector == "broken_ocean" for g in gaps)


def test_sample_config_builds_only_sample_connectors() -> None:
    settings = load_settings("config/connectors.sample.yaml")
    conns = build_connectors(settings)
    assert {c.name for c in conns} == {
        "sample_landings",
        "sample_assessment",
        "sample_ocean",
        "sample_literature",
    }

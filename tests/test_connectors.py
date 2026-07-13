"""Connector tests, run offline against recorded response shapes (DESIGN_PROMPT §13).

Each connector is driven by an httpx ``MockTransport`` serving a cassette JSON
body, so tests are deterministic and need neither keys nor network.
"""

from __future__ import annotations

import httpx
import pytest

from fra.connectors.assessment_ram import RamLegacyConnector
from fra.connectors.base import ConnectorConfig
from fra.connectors.landings_fao import FaoLandingsConnector
from fra.connectors.literature_crossref import CrossrefConnector
from fra.connectors.literature_openalex import OpenAlexConnector
from fra.connectors.ocean_erddap import ErddapConnector
from fra.taxonomy import TaxonomyResolver
from tests.conftest import load_cassette, mock_http


@pytest.fixture
def offline_resolver(tmp_path) -> TaxonomyResolver:
    return TaxonomyResolver(tmp_path, allow_network=False)


async def test_crossref_parses_and_normalizes_scores(plan, tmp_path) -> None:
    client = mock_http({"/works": httpx.Response(200, json=load_cassette("crossref_hake"))})
    conn = CrossrefConnector(
        ConnectorConfig(name="crossref", domain="literature", base_url="https://api.crossref.org"),
        cache_dir=tmp_path,
        client=client,
    )
    refs = await conn.fetch(plan)
    assert len(refs) == 2
    assert refs[0].relevance_score == 1.0  # top score normalized to 1
    assert refs[1].relevance_score == pytest.approx(0.5, abs=0.01)
    assert "<jats" not in (refs[0].abstract or "")  # JATS stripped
    assert refs[0].doi == "10.1093/icesjms/fsx100"


async def test_openalex_deinverts_abstract(plan, tmp_path) -> None:
    client = mock_http({"/works": httpx.Response(200, json=load_cassette("openalex_hake"))})
    conn = OpenAlexConnector(
        ConnectorConfig(name="openalex", domain="literature", base_url="https://api.openalex.org"),
        cache_dir=tmp_path,
        client=client,
    )
    refs = await conn.fetch(plan)
    assert len(refs) == 2
    assert refs[0].abstract == "We assess European hake and find overfishing."
    assert refs[0].authors == ["Anna Rossi", "Marco Bianchi"]
    assert 0.0 <= refs[1].relevance_score <= 1.0


async def test_erddap_aggregates_annual_means(plan, tmp_path) -> None:
    client = mock_http({"/griddap": httpx.Response(200, json=load_cassette("erddap_sst"))})
    cfg = ConnectorConfig(
        name="erddap_ocean",
        domain="ocean",
        base_url="https://erddap.example/erddap",
        options={
            "variables": {
                "sst": {
                    "dataset_id": "testSST",
                    "field": "sea_surface_temperature",
                    "unit": "degree_C",
                }
            },
            "area_bounds": {"37.2": {"lat_min": 40, "lat_max": 44, "lon_min": 15, "lon_max": 19}},
        },
    )
    conn = ErddapConnector(cfg, cache_dir=tmp_path, client=client)
    series = await conn.fetch(plan)
    assert len(series) == 1
    s = series[0]
    assert s.variable == "sst"
    # 2010 mean of 18.4 & 18.6 = 18.5; null dropped in 2012
    means = {tp.date.year: tp.value for tp in s.values}
    assert means[2010] == pytest.approx(18.5)
    assert means[2012] == pytest.approx(19.3)


async def test_erddap_gap_when_no_bounds(plan, tmp_path) -> None:
    client = mock_http({"/griddap": httpx.Response(200, json=load_cassette("erddap_sst"))})
    cfg = ConnectorConfig(
        name="erddap_ocean",
        domain="ocean",
        base_url="https://erddap.example/erddap",
        options={"variables": {"sst": {"dataset_id": "t", "field": "sea_surface_temperature"}}},
    )
    conn = ErddapConnector(cfg, cache_dir=tmp_path, client=client)
    # No area_bounds configured -> connector returns nothing (a gap), not a guess.
    assert await conn.fetch(plan) == []


async def test_fao_landings_skips_null_tonnes(plan, tmp_path, offline_resolver) -> None:
    client = mock_http({"/query": httpx.Response(200, json=load_cassette("fao_landings"))})
    conn = FaoLandingsConnector(
        ConnectorConfig(name="fao_landings", domain="landings", base_url="https://fao.example"),
        cache_dir=tmp_path,
        client=client,
        resolver=offline_resolver,
    )
    recs = await conn.fetch(plan)
    # 3 rows, one with null tonnes -> 2 records; never fabricated
    assert len(recs) == 2
    assert all(r.tonnes > 0 for r in recs)
    assert recs[0].taxon.aphia_id == 126484  # resolved via seed table


async def test_ram_assessment_keeps_missing_reference_points(
    plan, tmp_path, offline_resolver
) -> None:
    client = mock_http({"/assessments": httpx.Response(200, json=load_cassette("ram_assessment"))})
    conn = RamLegacyConnector(
        ConnectorConfig(name="ram_legacy", domain="assessment", base_url="https://ram.example"),
        cache_dir=tmp_path,
        client=client,
        resolver=offline_resolver,
    )
    recs = await conn.fetch(plan)
    assert len(recs) == 3
    assert recs[2].f_msy is None and recs[2].b_msy is None  # missing stays missing


async def test_retry_then_success(plan, tmp_path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json=load_cassette("crossref_hake"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    conn = CrossrefConnector(
        ConnectorConfig(name="crossref", domain="literature", base_url="https://api.crossref.org"),
        cache_dir=tmp_path,
        client=client,
    )
    refs = await conn.fetch(plan)
    assert calls["n"] == 2  # one 503, one success
    assert len(refs) == 2

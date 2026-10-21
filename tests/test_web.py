"""Tests for the ``fra ui`` web server: API, run lifecycle, and request checks.

The server runs on a free local port in a background thread and drives real
runs against the bundled sample data with the offline LLM, so no keys and no
network are needed.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from fra.config import load_settings
from fra.pipeline import build_pipeline
from fra.web import RunManager, make_server
from fra.web.server import UIServer

REPO = Path(__file__).resolve().parent.parent
QUESTION = "Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2020"


@pytest.fixture
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, UIServer]]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    server = make_server(RunManager(REPO, tmp_path), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        server.server_close()


def get(url: str) -> tuple[int, dict[str, str], bytes]:
    try:
        with urlopen(url, timeout=10) as res:
            return res.status, dict(res.headers), res.read()
    except HTTPError as err:
        return err.code, dict(err.headers), err.read()


def get_json(url: str) -> tuple[int, Any]:
    status, _, body = get(url)
    return status, json.loads(body)


def post(url: str, payload: object, content_type: str = "application/json") -> tuple[int, Any]:
    req = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read())
    except HTTPError as err:
        return err.code, json.loads(err.read())


def wait_for(base: str, run_id: str, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, run = get_json(f"{base}/api/run?{urlencode({'id': run_id})}")
        if run["status"] != "running":
            return dict(run)
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_page_and_assets_are_served(ui: tuple[str, UIServer]) -> None:
    base, _ = ui
    status, headers, body = get(f"{base}/")
    assert status == 200
    assert b"Nereus" in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    for name, ctype in [("app.js", "text/javascript"), ("app.css", "text/css")]:
        status, headers, _ = get(f"{base}/static/{name}")
        assert status == 200
        assert headers["Content-Type"].startswith(ctype)


def test_info_lists_sources_and_examples(ui: tuple[str, UIServer]) -> None:
    base, _ = ui
    status, info = get_json(f"{base}/api/info")
    assert status == 200
    sources = {s["id"]: s for s in info["sources"]}
    assert sources["sample"]["available"] is True
    assert info["examples"]
    assert info["claude"]["available"] is False


def test_sample_run_produces_report_and_files(ui: tuple[str, UIServer]) -> None:
    base, _ = ui
    status, body = post(f"{base}/api/runs", {"question": QUESTION, "source": "sample"})
    assert status == 202
    run = wait_for(base, body["id"])

    assert run["status"] == "done", run.get("error")
    assert run["question"] == QUESTION
    assert run["source_label"] == "Sample data"
    assert run["report"]["final_report"]["citations"]
    events = {e["event"] for e in run["events"]}
    assert {"start", "phase", "retrieval_complete", "done"} <= events

    figure = run["report"]["final_report"]["figures"][0]
    name = Path(figure["path"]).name
    status, headers, data = get(
        f"{base}/file?{urlencode({'id': run['id'], 'path': f'figures/{name}'})}"
    )
    assert status == 200 and headers["Content-Type"] == "image/png" and data[:4] == b"\x89PNG"

    query = urlencode({"id": run["id"], "path": "report.md", "download": "1"})
    status, headers, data = get(f"{base}/file?{query}")
    assert status == 200 and "attachment" in headers["Content-Disposition"]
    assert data.startswith(b"# Fisheries Research Report")

    _, listing = get_json(f"{base}/api/runs")
    assert listing["runs"][0]["id"] == run["id"]


def test_vague_question_asks_for_clarification(ui: tuple[str, UIServer]) -> None:
    base, _ = ui
    _, body = post(f"{base}/api/runs", {"question": "Tell me about the ocean", "source": "sample"})
    run = wait_for(base, body["id"])
    assert run["status"] == "clarification"
    assert run["clarification"]
    assert run["report"] is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"question": "   "}, 400),
        ({"question": QUESTION, "source": "nope"}, 400),
        ({"question": QUESTION, "use_claude": True}, 400),  # no key in the environment
        ({"question": "x" * 1001}, 400),
    ],
)
def test_bad_run_requests_are_rejected(
    ui: tuple[str, UIServer], payload: dict[str, object], expected: int
) -> None:
    base, _ = ui
    status, body = post(f"{base}/api/runs", payload)
    assert status == expected
    assert body["error"]


def test_only_one_run_at_a_time(ui: tuple[str, UIServer]) -> None:
    base, server = ui
    server.manager._active = "someone-else"
    status, body = post(f"{base}/api/runs", {"question": QUESTION, "source": "sample"})
    assert status == 409
    assert "still running" in body["error"]


def test_non_json_post_is_rejected(ui: tuple[str, UIServer]) -> None:
    base, _ = ui
    status, _ = post(f"{base}/api/runs", {"question": QUESTION}, content_type="text/plain")
    assert status == 415


def test_foreign_host_and_origin_are_rejected(ui: tuple[str, UIServer]) -> None:
    _, server = ui
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    conn.request("GET", "/api/runs", headers={"Host": "evil.example"})
    assert conn.getresponse().status == 403
    conn.close()

    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    body = json.dumps({"question": QUESTION})
    headers = {"Content-Type": "application/json", "Origin": "http://evil.example"}
    conn.request("POST", "/api/runs", body=body, headers=headers)
    assert conn.getresponse().status == 403
    conn.close()


@pytest.mark.parametrize(
    ("run_id", "path"),
    [
        ("../..", "report.md"),
        ("..%2F..", "report.md"),
        ("cli-run", "../../pyproject.toml"),
        ("cli-run", "ui_run.json"),
        ("cli-run", "figures/../report.md"),
        ("missing", "report.md"),
    ],
)
def test_file_access_is_confined_to_runs(ui: tuple[str, UIServer], run_id: str, path: str) -> None:
    base, server = ui
    (server.manager.out_root / "cli-run").mkdir(exist_ok=True)
    (server.manager.out_root / "cli-run" / "report.md").write_text("ok", encoding="utf-8")
    status, _, _ = get(f"{base}/file?{urlencode({'id': run_id, 'path': path})}")
    assert status == 404


def test_runs_started_from_the_cli_are_listed(ui: tuple[str, UIServer]) -> None:
    base, server = ui
    out = server.manager.out_root
    pipeline = build_pipeline(
        load_settings(REPO / "config" / "connectors.sample.yaml"), offline=True, out_root=out
    )
    asyncio.run(pipeline.orchestrator.run(QUESTION, run_id="from-cli"))

    _, listing = get_json(f"{base}/api/runs")
    run = next(r for r in listing["runs"] if r["id"] == "from-cli")
    assert run["status"] == "done"
    assert run["question"] == QUESTION

"""A small local web server for running and reading reports (``fra ui``).

Standard library only, so it adds no dependencies. It serves one page plus a
JSON API, runs one report at a time in a background thread, and follows each
run's progress through the ``run_log.jsonl`` the orchestrator already writes.

It binds to 127.0.0.1 by default and only answers requests addressed to that
host. API keys are read from the environment, never accepted over HTTP.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import threading
import uuid
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from fra.config import Settings, load_settings
from fra.orchestrator import Phase
from fra.pipeline import build_pipeline, has_api_key

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
META_FILE = "ui_run.json"
MAX_BODY_BYTES = 16_384
MAX_QUESTION_CHARS = 1_000
DEMO_SHIM = Path("demo") / "serve_local_sources.py"
DEMO_SHIM_PORT = 8899

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_FIGURE_RE = re.compile(r"^figures/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.png$")
_RUN_FILES = {"report.md", "report.json", "run_log.jsonl"}
_LOOPBACK = {"127.0.0.1", "localhost"}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".jsonl": "application/x-ndjson; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}

_PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


@dataclass(frozen=True)
class Source:
    """A data-source preset the page offers, backed by a connectors YAML file."""

    id: str
    label: str
    description: str
    config: str
    needs_internet: bool
    uses_demo_data: bool = False


SOURCES: tuple[Source, ...] = (
    Source(
        id="sample",
        label="Sample data",
        description=(
            "Synthetic European hake data for FAO 37.2, bundled with Nereus. "
            "Works offline and finishes in seconds."
        ),
        config="config/connectors.sample.yaml",
        needs_internet=False,
    ),
    Source(
        id="demo",
        label="Real EU data",
        description=(
            "Eurostat landings and a GFCM assessment for European hake in FAO 37.2, "
            "plus live papers from Crossref and OpenAlex and ocean data from ERDDAP."
        ),
        config="config/connectors.demo.yaml",
        needs_internet=True,
        uses_demo_data=True,
    ),
    Source(
        id="live",
        label="Live sources",
        description="The connectors configured in config/connectors.yaml.",
        config="config/connectors.yaml",
        needs_internet=True,
    ),
)

EXAMPLES: tuple[str, ...] = (
    "Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2020",
    "How have European hake landings in FAO 37.2 changed from 2010 to 2020?",
    "Is sea surface temperature associated with European hake biomass in FAO 37.2 "
    "between 2012 and 2020?",
)


class BusyError(RuntimeError):
    """Raised when a run is requested while another one is still going."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        text = (run_dir / "run_log.jsonl").read_text(encoding="utf-8")
    except OSError:
        return events
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue  # the orchestrator may be mid-write on the last line
        if isinstance(item, dict):
            events.append(item)
    return events


# Windows refuses to replace a file another thread has open, so reads and
# writes of the run metadata never overlap.
_META_LOCK = threading.Lock()


def _read_meta(run_dir: Path) -> dict[str, Any]:
    with _META_LOCK:
        meta = _read_json(run_dir / META_FILE)
    return meta if isinstance(meta, dict) else {}


def _write_meta(run_dir: Path, meta: dict[str, Any]) -> None:
    text = json.dumps(meta, indent=2, default=str)
    tmp = run_dir / (META_FILE + ".tmp")
    with _META_LOCK:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(run_dir / META_FILE)


class RunManager:
    """Starts runs, one at a time, and reads finished and running runs from disk."""

    def __init__(self, project_root: Path, out_root: Path) -> None:
        self.project_root = project_root.resolve()
        out = out_root if out_root.is_absolute() else self.project_root / out_root
        self.out_root = out.resolve()
        self._lock = threading.Lock()
        self._active: str | None = None
        self._shim: Any = None

    # -- presets -------------------------------------------------------------

    def _unavailable_reason(self, source: Source) -> str | None:
        if not (self.project_root / source.config).is_file():
            return f"{source.config} was not found. Start fra ui from the Nereus project folder."
        if source.uses_demo_data and not (self.project_root / DEMO_SHIM).is_file():
            return "The demo/ folder was not found. Start fra ui from the Nereus project folder."
        return None

    def info(self) -> dict[str, Any]:
        sources = []
        for s in SOURCES:
            reason = self._unavailable_reason(s)
            sources.append({**asdict(s), "available": reason is None, "unavailable": reason})
        return {
            "sources": sources,
            "examples": list(EXAMPLES),
            "claude": {"available": has_api_key(), "model": Settings().model},
            "active_run": self.active_run,
        }

    @property
    def active_run(self) -> str | None:
        with self._lock:
            return self._active

    # -- starting runs ---------------------------------------------------------

    def start(self, question: str, source_id: str, use_claude: bool) -> str:
        source = next((s for s in SOURCES if s.id == source_id), None)
        if source is None:
            raise ValueError(f"Unknown data source: {source_id!r}.")
        reason = self._unavailable_reason(source)
        if reason is not None:
            raise ValueError(reason)
        if use_claude and not has_api_key():
            raise ValueError("ANTHROPIC_API_KEY is not set, so Claude can't write this report.")

        with self._lock:
            if self._active is not None:
                raise BusyError("Another report is still running. Try again when it finishes.")
            run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
            self._active = run_id

        try:
            run_dir = self.out_root / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            meta: dict[str, Any] = {
                "id": run_id,
                "question": question,
                "source": source.id,
                "source_label": source.label,
                "writer": "claude" if use_claude else "offline",
                "model": None,
                "status": "running",
                "started_at": _now(),
                "finished_at": None,
                "error": None,
                "clarification": [],
            }
            _write_meta(run_dir, meta)
            worker = threading.Thread(
                target=self._work,
                args=(run_dir, meta, source, use_claude),
                name=f"fra-run-{run_id}",
                daemon=True,
            )
            worker.start()
        except Exception:
            with self._lock:
                self._active = None
            raise
        return run_id

    def _work(self, run_dir: Path, meta: dict[str, Any], source: Source, use_claude: bool) -> None:
        try:
            if source.uses_demo_data:
                self._ensure_demo_shim()
            settings = load_settings(self.project_root / source.config)
            pipeline = build_pipeline(settings, offline=not use_claude, out_root=self.out_root)
            meta["model"] = pipeline.model
            meta["connectors"] = pipeline.connectors
            _write_meta(run_dir, meta)

            result = asyncio.run(pipeline.orchestrator.run(meta["question"], run_id=meta["id"]))
            if result.phase == Phase.DONE:
                meta["status"] = "done"
            elif result.phase == Phase.CLARIFICATION_NEEDED:
                meta["status"] = "clarification"
                meta["clarification"] = list(result.clarification_questions)
            else:
                meta["status"] = "failed"
                meta["error"] = (
                    "The run stopped before the report was written, most likely because it "
                    "hit the time limit in the config. The run log shows the last step reached."
                )
        except Exception as exc:  # surface any failure on the page rather than losing it
            logger.exception("run %s failed", meta["id"])
            meta["status"] = "failed"
            meta["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            meta["finished_at"] = _now()
            try:
                _write_meta(run_dir, meta)
            finally:
                with self._lock:
                    self._active = None

    def _ensure_demo_shim(self) -> None:
        """Start the demo's local data server once; reuse it if it is already up."""
        with self._lock:
            if self._shim is not None:
                return
            path = self.project_root / DEMO_SHIM
            spec = importlib.util.spec_from_file_location("fra_demo_sources", path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"could not load {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            try:
                self._shim = module.start(port=DEMO_SHIM_PORT)
            except OSError:
                # The port is taken, most likely by a shim started by hand. Use that one.
                logger.info("demo data port %d already in use; reusing it", DEMO_SHIM_PORT)
                self._shim = False

    def close(self) -> None:
        with self._lock:
            shim, self._shim = self._shim, None
        if shim:
            shim.shutdown()

    # -- reading runs ------------------------------------------------------------

    def resolve_run(self, run_id: str) -> Path | None:
        """Map a run id (``<id>`` or ``<folder>/<id>``) to its directory, safely."""
        parts = run_id.split("/")
        if not 1 <= len(parts) <= 2 or not all(_SEGMENT_RE.match(p) for p in parts):
            return None
        path = self.out_root.joinpath(*parts).resolve()
        if not path.is_relative_to(self.out_root) or not path.is_dir():
            return None
        return path

    def _run_dirs(self) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = []
        if not self.out_root.is_dir():
            return found

        def is_run(d: Path) -> bool:
            return (d / "run_log.jsonl").is_file() or (d / META_FILE).is_file()

        for child in self.out_root.iterdir():
            if not child.is_dir() or not _SEGMENT_RE.match(child.name):
                continue
            if is_run(child):
                found.append((child.name, child))
                continue
            for grandchild in child.iterdir():  # e.g. runs/demo/<id> from demo/run_demo.py
                if (
                    grandchild.is_dir()
                    and _SEGMENT_RE.match(grandchild.name)
                    and is_run(grandchild)
                ):
                    found.append((f"{child.name}/{grandchild.name}", grandchild))
        return found

    def _summary(
        self, run_id: str, run_dir: Path, events: list[dict[str, Any]], report: Any
    ) -> dict[str, Any]:
        meta = _read_meta(run_dir)
        names = {e.get("event") for e in events}
        start_event = next((e for e in events if e.get("event") == "start"), {})

        question = meta.get("question") or start_event.get("question")
        if not question and isinstance(report, dict):
            question = report.get("final_report", {}).get("question")

        status = meta.get("status")
        error = meta.get("error")
        clarification = meta.get("clarification") or []
        if status == "running" and self.active_run != run_id:
            status, error = "failed", "Nereus was closed before this run finished."
        if not status:  # a run started from the command line has no UI metadata
            if (run_dir / "report.json").is_file():
                status = "done"
            elif "clarification_needed" in names:
                status = "clarification"
                ev = next(e for e in events if e.get("event") == "clarification_needed")
                clarification = ev.get("questions") or []
            else:
                status = "failed"
                error = "This run did not produce a report."

        started = meta.get("started_at") or (events[0].get("ts") if events else None)
        if not started:
            started = datetime.fromtimestamp(run_dir.stat().st_mtime, timezone.utc).isoformat()

        return {
            "id": run_id,
            "question": question or "(question not recorded)",
            "status": status,
            "started_at": started,
            "finished_at": meta.get("finished_at"),
            "source": meta.get("source"),
            "source_label": meta.get("source_label"),
            "writer": meta.get("writer"),
            "model": meta.get("model"),
            "error": error,
            "clarification": clarification,
        }

    def list_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        runs = []
        for run_id, run_dir in self._run_dirs():
            report = None
            needs_report = not _read_meta(run_dir).get("question")
            if needs_report:
                report = _read_json(run_dir / "report.json")
            runs.append(self._summary(run_id, run_dir, _read_events(run_dir), report))
        runs.sort(key=lambda r: str(r["started_at"]), reverse=True)
        return runs[:limit]

    def run_detail(self, run_id: str) -> dict[str, Any] | None:
        run_dir = self.resolve_run(run_id)
        if run_dir is None:
            return None
        events = _read_events(run_dir)
        report = _read_json(run_dir / "report.json")
        detail = self._summary(run_id, run_dir, events, report)
        detail["events"] = events
        detail["report"] = report if isinstance(report, dict) else None
        return detail


# -- HTTP layer --------------------------------------------------------------------


class UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], manager: RunManager, *, check_host: bool) -> None:
        super().__init__(address, _Handler)
        self.manager = manager
        self.check_host = check_host

    def allowed_hosts(self) -> set[str]:
        return {f"{h}:{self.server_port}" for h in _LOOPBACK}


def _one(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or [""]
    return values[0]


class _Handler(BaseHTTPRequestHandler):
    server_version = "Nereus"
    sys_version = ""

    @property
    def app(self) -> UIServer:
        return cast(UIServer, self.server)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s " + format, self.address_string(), *args)

    # -- request checks ---------------------------------------------------------

    def _host_ok(self) -> bool:
        if not self.app.check_host:
            return True
        return self.headers.get("Host", "") in self.app.allowed_hosts()

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None or not self.app.check_host:
            return True
        return origin in {f"http://{h}" for h in self.app.allowed_hosts()}

    # -- responses -----------------------------------------------------------------

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    def _static(self, name: str) -> None:
        path = STATIC_DIR / name
        if not _SEGMENT_RE.match(name) or not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Not found.")
            return
        ctype = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        extra = {"Content-Security-Policy": _PAGE_CSP, "X-Frame-Options": "DENY"}
        self._send(HTTPStatus.OK, path.read_bytes(), ctype, extra)

    def _run_file(self, run_id: str, rel: str, download: bool) -> None:
        run_dir = self.app.manager.resolve_run(run_id)
        if run_dir is None or not (rel in _RUN_FILES or _FIGURE_RE.match(rel)):
            self._error(HTTPStatus.NOT_FOUND, "Not found.")
            return
        path = (run_dir / rel).resolve()
        if not path.is_relative_to(run_dir) or not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Not found.")
            return
        extra: dict[str, str] = {}
        if download:
            name = f"{run_id.replace('/', '-')}-{path.name}"
            extra["Content-Disposition"] = f'attachment; filename="{name}"'
        ctype = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(HTTPStatus.OK, path.read_bytes(), ctype, extra)

    # -- routes ----------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "Open Nereus at its local address.")
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        manager = self.app.manager
        try:
            if url.path in ("/", "/index.html"):
                self._static("index.html")
            elif url.path.startswith("/static/"):
                self._static(url.path.removeprefix("/static/"))
            elif url.path == "/api/info":
                self._json(HTTPStatus.OK, manager.info())
            elif url.path == "/api/runs":
                self._json(HTTPStatus.OK, {"runs": manager.list_runs()})
            elif url.path == "/api/run":
                detail = manager.run_detail(_one(query, "id"))
                if detail is None:
                    self._error(HTTPStatus.NOT_FOUND, "There is no report with that id.")
                else:
                    self._json(HTTPStatus.OK, detail)
            elif url.path == "/file":
                download = _one(query, "download") == "1"
                self._run_file(_one(query, "id"), _one(query, "path"), download)
            else:
                self._error(HTTPStatus.NOT_FOUND, "Not found.")
        except Exception:
            logger.exception("error handling %s", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Nereus hit an error. See the terminal.")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or not self._origin_ok():
            self._error(HTTPStatus.FORBIDDEN, "Open Nereus at its local address.")
            return
        if urlparse(self.path).path != "/api/runs":
            self._error(HTTPStatus.NOT_FOUND, "Not found.")
            return
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send the request as JSON.")
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY_BYTES:
            self._error(HTTPStatus.BAD_REQUEST, "The request body is missing or too large.")
            return
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._error(HTTPStatus.BAD_REQUEST, "The request body is not a JSON object.")
            return

        question = str(data.get("question") or "").strip()
        if not question:
            self._error(HTTPStatus.BAD_REQUEST, "Type a research question first.")
            return
        if len(question) > MAX_QUESTION_CHARS:
            self._error(
                HTTPStatus.BAD_REQUEST,
                f"Keep the question under {MAX_QUESTION_CHARS} characters.",
            )
            return
        try:
            run_id = self.app.manager.start(
                question, str(data.get("source") or "sample"), bool(data.get("use_claude"))
            )
        except BusyError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
            return
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._json(HTTPStatus.ACCEPTED, {"id": run_id})


def make_server(manager: RunManager, host: str = "127.0.0.1", port: int = 8765) -> UIServer:
    """Create (but don't start) the server. Port 0 picks a free port."""
    return UIServer((host, port), manager, check_host=host in _LOOPBACK)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    out_root: Path = Path("runs"),
    project_root: Path | None = None,
    open_browser: bool = True,
) -> int:
    """Run the web interface until Ctrl+C. Returns a process exit code."""
    manager = RunManager(project_root or Path.cwd(), out_root)
    try:
        server = make_server(manager, host, port)
    except OSError as exc:
        print(f"✗  Could not start on {host}:{port} ({exc.strerror or exc}).")
        print(f"   Another program may be using the port. Try: fra ui --port {port + 1}")
        return 1

    shown_host = "127.0.0.1" if host in _LOOPBACK else host
    url = f"http://{shown_host}:{server.server_port}/"
    print(f"• Nereus is running at {url}")
    print(f"• Reports are saved in {manager.out_root}")
    if not has_api_key():
        print("• No ANTHROPIC_API_KEY found: reports use the offline rule engine.")
    print("  Press Ctrl+C to stop.\n")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n• Stopped.")
    finally:
        server.server_close()
        manager.close()
    return 0

"""Tiny local HTTP shim that serves REAL fisheries data to the fra connectors.

The `fao_landings` and `ram_legacy` connectors fetch from an HTTP endpoint that
speaks a small JSON schema (see their module docstrings). Public portals don't
expose that exact schema, so this shim serves pre-fetched REAL data from
`demo/data/*.json` on localhost, letting the demo run against genuine numbers
without a bespoke API.

    GET /query        -> landings rows (Eurostat FAO 37.2 European hake)
    GET /assessments  -> assessment rows (GFCM WGSAD 17-18 hake; see file header)

Run standalone:  python demo/serve_local_sources.py   (serves on 127.0.0.1:8899)
Or import `start` from demo/run_demo.py to launch it in a background thread.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_DATA = Path(__file__).resolve().parent / "data"
_LANDINGS = json.loads((_DATA / "landings_fao37_hake.json").read_text(encoding="utf-8"))
_ASSESSMENT = json.loads((_DATA / "assessment_hke_gsa17_18.json").read_text(encoding="utf-8"))

_ROUTES = {"/query": _LANDINGS, "/assessments": _ASSESSMENT}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = urlparse(self.path).path.rstrip("/") or "/"
        payload = _ROUTES.get(path)
        if payload is None:
            self.send_error(404, f"no such endpoint: {path}")
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence per-request logging
        return


def start(host: str = "127.0.0.1", port: int = 8899) -> ThreadingHTTPServer:
    """Start the shim in a daemon thread and return the server (call .shutdown())."""
    server = ThreadingHTTPServer((host, port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    srv = start()
    print(f"Local source shim serving REAL data on http://{srv.server_address[0]}:{srv.server_address[1]}")
    print("  GET /query        -> Eurostat FAO 37.2 European hake landings")
    print("  GET /assessments  -> GFCM 17-18 hake assessment (representative; see data file header)")
    print("Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()

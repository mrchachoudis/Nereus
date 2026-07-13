"""One-command LIVE DEMO for Nereus / fra.

Starts the local data shim (REAL Eurostat landings + GFCM assessment) in a
background thread, then runs the full pipeline OFFLINE (deterministic LLM, no API
key) against that data plus the live Crossref / OpenAlex / ERDDAP public APIs.

    python demo/run_demo.py

Output: a timestamped run directory under ./runs/demo/ with report.md,
report.json, figures/, and run_log.jsonl.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "demo"))

QUESTION = (
    "Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2020"
)
CONFIG = _REPO / "config" / "connectors.demo.yaml"
OUT = _REPO / "runs" / "demo"


def main() -> int:
    import serve_local_sources
    from fra.cli import main as fra_main

    server = serve_local_sources.start(port=8899)
    print(f"• Local data shim up on http://127.0.0.1:8899 (real Eurostat + GFCM data)\n")
    try:
        return fra_main(
            [
                "run",
                QUESTION,
                "--config",
                str(CONFIG),
                "--offline",
                "--out",
                str(OUT),
            ]
        )
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

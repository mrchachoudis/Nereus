"""``fra`` command-line entry point (DESIGN_PROMPT §12).

    fra ui                      # open the web interface in your browser
    fra run "Assess stock status and environmental drivers of European hake in
             FAO 37.2, 2010-2023" --config config/connectors.yaml --out ./runs/

Reads API keys from the environment (never from arguments). If no
``ANTHROPIC_API_KEY`` is set, or ``--offline`` is passed, the deterministic
offline backend is used so the pipeline still runs, keyless and reproducibly.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from fra.config import load_settings
from fra.orchestrator import Phase
from fra.pipeline import build_pipeline


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.max_revisions is not None:
        settings.runtime.max_revision_rounds = args.max_revisions

    pipeline = build_pipeline(settings, offline=args.offline, out_root=args.out, progress=True)
    if pipeline.offline:
        print("• LLM: deterministic OFFLINE backend (no ANTHROPIC_API_KEY or --offline set).")
    else:
        print(f"• LLM: Anthropic backend, model={pipeline.model}")
    print(f"• Connectors enabled: {', '.join(pipeline.connectors) or '(none)'}")
    print(f"• Running: {args.question!r}\n")
    result = await pipeline.orchestrator.run(args.question)

    print()
    if result.phase == Phase.CLARIFICATION_NEEDED:
        print("⚠  The question is under-specified. Please clarify:")
        for q in result.clarification_questions:
            print(f"   - {q}")
        return 2
    if result.phase == Phase.FAILED:
        print("✗  Run failed (see run_log.jsonl).")
        return 1

    assert result.out_dir is not None
    print(f"✓  Report written to {result.out_dir}")
    print(f"   - {result.out_dir / 'report.md'}")
    print(f"   - {result.out_dir / 'report.json'}")
    print(f"   - {result.out_dir / 'figures'}/")
    print(f"   - {result.out_dir / 'run_log.jsonl'}")
    bb = result.blackboard
    print(
        f"   ({len(bb.analyses)} analyses, {len(bb.references)} references, "
        f"{len(bb.coverage_gaps)} coverage gaps)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fra", description="Fisheries Research Agents")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the pipeline on a research question.")
    run.add_argument("question", help="The research question, quoted.")
    run.add_argument(
        "--config", default="config/connectors.yaml", type=Path, help="Path to connectors.yaml."
    )
    run.add_argument("--out", default="runs", type=Path, help="Run output root directory.")
    run.add_argument(
        "--max-revisions", type=int, default=None, help="Override synthesis↔critic revision cap."
    )
    run.add_argument(
        "--offline", action="store_true", help="Force the deterministic offline LLM backend."
    )
    run.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")

    ui = sub.add_parser("ui", help="Open the web interface in your browser.")
    ui.add_argument("--port", type=int, default=8765, help="Port to serve on (default 8765).")
    ui.add_argument(
        "--host",
        default="127.0.0.1",
        help="Address to bind. The default keeps Nereus private to this computer.",
    )
    ui.add_argument("--out", default="runs", type=Path, help="Where reports are saved.")
    ui.add_argument("--no-browser", action="store_true", help="Don't open a browser tab.")
    ui.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles may default to a legacy codepage; force UTF-8 so status
    # output and report paths render regardless of locale.
    import contextlib

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "ui":
        from fra.web import serve

        return serve(
            host=args.host, port=args.port, out_root=args.out, open_browser=not args.no_browser
        )
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

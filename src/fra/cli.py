"""``fra`` command-line entry point (DESIGN_PROMPT §12).

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
import os
import sys
from pathlib import Path

from fra.config import Settings, load_settings
from fra.connectors import build_connectors
from fra.llm import LLMBackend, LLMClient
from fra.orchestrator import Orchestrator, Phase
from fra.taxonomy import TaxonomyResolver


def _build_llm(settings: Settings, *, offline: bool) -> tuple[LLMClient, bool]:
    """Return (client, is_offline)."""
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if offline or not has_key:
        from fra.offline import DeterministicBackend

        backend: LLMBackend = DeterministicBackend()
        return (
            LLMClient(
                backend,
                model=f"offline/{settings.model}",
                max_tokens=settings.llm.max_tokens,
                temperature=settings.llm.temperature,
            ),
            True,
        )
    from fra.llm import AnthropicBackend

    anthropic_backend = AnthropicBackend(timeout=settings.llm.timeout_s)
    return (
        LLMClient(
            anthropic_backend,
            model=settings.model,
            max_tokens=settings.llm.max_tokens,
            temperature=settings.llm.temperature,
        ),
        False,
    )


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.max_revisions is not None:
        settings.runtime.max_revision_rounds = args.max_revisions

    llm, is_offline = _build_llm(settings, offline=args.offline)
    if is_offline:
        print("• LLM: deterministic OFFLINE backend (no ANTHROPIC_API_KEY or --offline set).")
    else:
        print(f"• LLM: Anthropic backend, model={settings.model}")

    resolver = TaxonomyResolver(settings.runtime.cache_dir, allow_network=not is_offline)
    connectors = build_connectors(settings, resolver=resolver)
    print(f"• Connectors enabled: {', '.join(c.name for c in connectors) or '(none)'}")

    orch = Orchestrator(settings, llm, connectors, out_root=args.out, progress=True)
    print(f"• Running: {args.question!r}\n")
    result = await orch.run(args.question)

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
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Wire settings into a ready-to-run :class:`~fra.orchestrator.Orchestrator`.

Shared by the ``fra run`` CLI and the ``fra ui`` web interface so both pick the
LLM backend and build connectors the same way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fra.config import Settings
from fra.connectors import build_connectors
from fra.llm import LLMBackend, LLMClient
from fra.orchestrator import Orchestrator
from fra.taxonomy import TaxonomyResolver


@dataclass
class Pipeline:
    orchestrator: Orchestrator
    offline: bool
    model: str
    connectors: list[str]


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def build_llm(settings: Settings, *, offline: bool) -> tuple[LLMClient, bool]:
    """Return ``(client, is_offline)``. Falls back to offline when no key is set."""
    if offline or not has_api_key():
        from fra.offline import DeterministicBackend

        backend: LLMBackend = DeterministicBackend()
        client = LLMClient(
            backend,
            model=f"offline/{settings.model}",
            max_tokens=settings.llm.max_tokens,
            temperature=settings.llm.temperature,
        )
        return client, True

    from fra.llm import AnthropicBackend

    client = LLMClient(
        AnthropicBackend(timeout=settings.llm.timeout_s),
        model=settings.model,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
    )
    return client, False


def build_pipeline(
    settings: Settings,
    *,
    offline: bool,
    out_root: str | Path,
    progress: bool = False,
) -> Pipeline:
    llm, is_offline = build_llm(settings, offline=offline)
    resolver = TaxonomyResolver(settings.runtime.cache_dir, allow_network=not is_offline)
    connectors = build_connectors(settings, resolver=resolver)
    orch = Orchestrator(settings, llm, connectors, out_root=out_root, progress=progress)
    model = "offline (deterministic)" if is_offline else settings.model
    return Pipeline(
        orchestrator=orch,
        offline=is_offline,
        model=model,
        connectors=[c.name for c in connectors],
    )

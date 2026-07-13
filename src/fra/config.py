"""Load and validate ``connectors.yaml`` into typed settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from fra.models import FraModel


class LLMSettings(FraModel):
    model: str = "claude-sonnet-5"
    max_tokens: int = 4096
    temperature: float = 0.2
    timeout_s: float = 120.0


class RuntimeSettings(FraModel):
    cache_dir: str = ".fra_cache"
    global_timeout_s: float = 1800.0
    agent_timeout_s: float = 300.0
    max_revision_rounds: int = 3


class ConnectorSettings(FraModel):
    domain: str
    enabled: bool = True
    base_url: str = ""
    rate_limit_per_s: float = 2.0
    notes: str | None = None
    # Any additional keys (variables, area_bounds, rows, ...) land here.
    options: dict[str, Any] = Field(default_factory=dict)


class Settings(FraModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    connectors: dict[str, ConnectorSettings] = Field(default_factory=dict)

    @property
    def model(self) -> str:
        """Effective LLM model: env override wins over config (DESIGN_PROMPT §7)."""
        return os.environ.get("FRA_LLM_MODEL", self.llm.model)


_KNOWN_CONNECTOR_KEYS = {"domain", "enabled", "base_url", "rate_limit_per_s", "notes"}


def load_settings(path: str | Path) -> Settings:
    """Parse a YAML config file into :class:`Settings`.

    Unknown per-connector keys are folded into ``options`` so a connector can
    carry source-specific config (dataset ids, bounding boxes) without changing
    this schema.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    connectors: dict[str, ConnectorSettings] = {}
    for name, block in (raw.get("connectors") or {}).items():
        block = dict(block or {})
        options = {k: v for k, v in block.items() if k not in _KNOWN_CONNECTOR_KEYS}
        known = {k: v for k, v in block.items() if k in _KNOWN_CONNECTOR_KEYS}
        connectors[name] = ConnectorSettings(options=options, **known)

    return Settings(
        llm=LLMSettings(**(raw.get("llm") or {})),
        runtime=RuntimeSettings(**(raw.get("runtime") or {})),
        connectors=connectors,
    )

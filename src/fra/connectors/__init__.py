"""Connector registry: builds enabled connector instances from settings.

A connector is skipped (and its domain reported as a coverage gap upstream) when
it is disabled in config or a required API key is absent from the environment
(DESIGN_PROMPT §8). Adding a regional source is a one-file change: implement the
:class:`~fra.connectors.base.Connector` protocol and register it in ``_BUILDERS``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from fra.config import ConnectorSettings, Settings
from fra.connectors.assessment_ram import RamLegacyConnector
from fra.connectors.base import Connector, ConnectorConfig
from fra.connectors.landings_fao import FaoLandingsConnector
from fra.connectors.literature_crossref import CrossrefConnector
from fra.connectors.literature_openalex import OpenAlexConnector
from fra.connectors.ocean_erddap import ErddapConnector
from fra.connectors.sample import (
    SampleAssessmentConnector,
    SampleLandingsConnector,
    SampleLiteratureConnector,
    SampleOceanConnector,
)
from fra.taxonomy import TaxonomyResolver

logger = logging.getLogger(__name__)

# Environment variables a connector requires to be usable; missing => auto-skip.
_REQUIRED_ENV: dict[str, list[str]] = {
    # Sources here are keyless or use a public server; none strictly required.
    "fao_landings": [],
    "ram_legacy": [],
    "erddap_ocean": [],
    "crossref": [],
    "openalex": [],
}


def _to_config(name: str, s: ConnectorSettings) -> ConnectorConfig:
    return ConnectorConfig(
        name=name,
        domain=s.domain,
        enabled=s.enabled,
        base_url=s.base_url,
        rate_limit_per_s=s.rate_limit_per_s,
        options=s.options,
    )


def _missing_env(name: str) -> list[str]:
    return [var for var in _REQUIRED_ENV.get(name, []) if not os.environ.get(var)]


def build_connectors(
    settings: Settings,
    *,
    resolver: TaxonomyResolver | None = None,
) -> list[Connector]:
    """Instantiate every enabled, satisfiable connector from ``settings``."""
    cache_dir = settings.runtime.cache_dir
    resolver = resolver or TaxonomyResolver(cache_dir, allow_network=True)

    # name -> factory. Sample connectors take no config/network.
    builders: dict[str, Callable[[ConnectorConfig], Connector]] = {
        "fao_landings": lambda c: FaoLandingsConnector(c, cache_dir=cache_dir, resolver=resolver),
        "ram_legacy": lambda c: RamLegacyConnector(c, cache_dir=cache_dir, resolver=resolver),
        "erddap_ocean": lambda c: ErddapConnector(c, cache_dir=cache_dir),
        "crossref": lambda c: CrossrefConnector(c, cache_dir=cache_dir),
        "openalex": lambda c: OpenAlexConnector(c, cache_dir=cache_dir),
    }
    sample_builders: dict[str, Callable[[], Connector]] = {
        "sample_landings": SampleLandingsConnector,
        "sample_assessment": SampleAssessmentConnector,
        "sample_ocean": SampleOceanConnector,
        "sample_literature": SampleLiteratureConnector,
    }

    out: list[Connector] = []
    for name, s in settings.connectors.items():
        if not s.enabled:
            logger.info("connector %s disabled in config; skipping", name)
            continue
        if name in sample_builders:
            out.append(sample_builders[name]())
            continue
        missing = _missing_env(name)
        if missing:
            logger.warning(
                "connector %s missing env %s; skipping (domain reported as gap)",
                name,
                ", ".join(missing),
            )
            continue
        builder = builders.get(name)
        if builder is None:
            logger.warning("connector %s has no registered builder; skipping", name)
            continue
        out.append(builder(_to_config(name, s)))
    return out


__all__ = [
    "Connector",
    "ConnectorConfig",
    "build_connectors",
    "FaoLandingsConnector",
    "RamLegacyConnector",
    "ErddapConnector",
    "CrossrefConnector",
    "OpenAlexConnector",
    "SampleLandingsConnector",
    "SampleAssessmentConnector",
    "SampleOceanConnector",
    "SampleLiteratureConnector",
]

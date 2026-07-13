"""Oceanography agent: environmental covariate series."""

from __future__ import annotations

from fra.agents._connector_agent import ConnectorAgent
from fra.connectors.base import Connector


class OceanographyAgent(ConnectorAgent):
    """Fetch and aggregate environmental covariates for the plan's bounds."""

    name = "oceanography"

    def __init__(self, connectors: list[Connector], *, timeout_s: float = 300.0) -> None:
        super().__init__(connectors, domains={"ocean"}, timeout_s=timeout_s)

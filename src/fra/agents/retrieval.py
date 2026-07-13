"""Data Retrieval agent: landings + stock-assessment records."""

from __future__ import annotations

from fra.agents._connector_agent import ConnectorAgent
from fra.connectors.base import Connector


class DataRetrievalAgent(ConnectorAgent):
    """Fetch landings and assessment records, normalized to the canonical models."""

    name = "data_retrieval"

    def __init__(self, connectors: list[Connector], *, timeout_s: float = 300.0) -> None:
        super().__init__(connectors, domains={"landings", "assessment"}, timeout_s=timeout_s)

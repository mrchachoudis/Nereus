"""Agent implementations (DESIGN_PROMPT §2)."""

from __future__ import annotations

from fra.agents.analysis import AnalysisAgent
from fra.agents.base import Agent, AgentError, BaseAgent, GuardResult
from fra.agents.critic import CriticAgent, mechanical_check
from fra.agents.literature import LiteratureAgent
from fra.agents.oceanography import OceanographyAgent
from fra.agents.planner import PlannerAgent
from fra.agents.retrieval import DataRetrievalAgent
from fra.agents.synthesis import SynthesisAgent

__all__ = [
    "Agent",
    "AgentError",
    "BaseAgent",
    "GuardResult",
    "PlannerAgent",
    "DataRetrievalAgent",
    "OceanographyAgent",
    "LiteratureAgent",
    "AnalysisAgent",
    "SynthesisAgent",
    "CriticAgent",
    "mechanical_check",
]

"""Planner agent: research question -> validated ResearchPlan."""

from __future__ import annotations

from fra.agents.base import AgentError, BaseAgent
from fra.agents.planning_heuristic import heuristic_plan
from fra.blackboard import Blackboard
from fra.llm import LLMClient, agent_system, agent_user, load_prompt
from fra.models import ResearchPlan


class PlannerAgent(BaseAgent):
    """Decompose the user's question into a typed plan (DESIGN_PROMPT §2)."""

    name = "planner"

    def __init__(self, llm: LLMClient, *, timeout_s: float = 120.0) -> None:
        super().__init__(timeout_s=timeout_s, max_retries=2)
        self._llm = llm
        self._template = load_prompt("planner")

    async def _run(self, bb: Blackboard) -> Blackboard:
        question = bb.question or (bb.plan.question if bb.plan else None)
        if not question:
            raise AgentError("planner: no question on the blackboard")

        # Deterministic first pass: grounds the model and provides an offline
        # fallback the backend can return verbatim.
        heuristic = heuristic_plan(question)
        context = {
            "question": question,
            "heuristic_plan": heuristic.model_dump(mode="json"),
        }
        plan = self._llm.structured(
            system=agent_system(self.name, self._template),
            user=agent_user(
                "Produce the ResearchPlan for the question below. The heuristic_plan "
                "in the context is a starting point; correct and extend it.",
                context,
            ),
            schema=ResearchPlan,
            agent=self.name,
        )
        bb.plan = plan
        if plan.needs_clarification:
            self.log.info("planner requests clarification: %s", plan.clarification_questions)
        else:
            self.log.info(
                "planner resolved %d taxa, %d areas, %s",
                len(plan.taxa),
                len(plan.areas),
                plan.time_range,
            )
        return bb

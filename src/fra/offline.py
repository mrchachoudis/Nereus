"""A deterministic, offline LLM backend.

This is NOT a language model. It satisfies :class:`~fra.llm.LLMBackend` by reading
the machine-readable context each agent embeds in its prompt and emitting valid
structured JSON by rule. It lets the whole pipeline run keyless and reproducibly
(for the committed example and for a quickstart with no API key), while every
number it emits is still drawn only from the supplied artifacts - the grounding
contract holds exactly as it would for a real model.

For the Planner it returns the heuristic plan already computed in context. For
Synthesis it composes a grounded, fully-cited report from the analyses and
records. For the Critic it defers to the mechanical check (returns pass with no
extra notes). A real deployment sets ANTHROPIC_API_KEY and uses
:class:`~fra.llm.AnthropicBackend` instead.
"""

from __future__ import annotations

import json
from typing import Any

from fra.llm import LLMBackend, LLMClient, LLMResult, Message, parse_agent_name, parse_context


class DeterministicBackend:
    """Rule-based backend used for offline/keyless runs and deterministic tests."""

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> LLMResult:
        agent = parse_agent_name(system) or "unknown"
        user = messages[-1].content if messages else ""
        context = parse_context(user)
        if not isinstance(context, dict):
            context = {}

        if agent == "planner":
            payload: Any = _plan(context)
        elif agent == "synthesis":
            payload = _synthesize(context)
        elif agent == "critic":
            payload = _criticize(context)
        else:  # pragma: no cover - defensive
            payload = {}

        text = json.dumps(payload)
        # rough token accounting so the run log has non-zero, plausible numbers
        return LLMResult(
            text=text,
            input_tokens=len(user) // 4,
            output_tokens=len(text) // 4,
            model=f"deterministic/{model}",
        )


def _plan(context: dict[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = context.get("heuristic_plan") or {
        "question": context.get("question", ""),
        "needs_clarification": True,
        "clarification_questions": ["Please restate the question."],
    }
    return plan


def _synthesize(context: dict[str, Any]) -> dict[str, Any]:
    run_id = context.get("run_id", "run")
    question = context.get("question", "")
    analyses: list[dict[str, Any]] = context.get("analyses", [])
    references: list[dict[str, Any]] = context.get("references", [])
    gaps: list[dict[str, Any]] = context.get("coverage_gaps", [])
    figures: list[dict[str, Any]] = context.get("figures", [])

    citations: list[dict[str, Any]] = []
    results_claims: list[dict[str, Any]] = []
    driver_claims: list[dict[str, Any]] = []

    for marker_n, a in enumerate(analyses, start=1):
        marker = f"[A{marker_n}]"
        citations.append(
            {"marker": marker, "ref_id": a["id"], "kind": "analysis", "detail": a["target"]}
        )
        claim = {"text": a["interpretation"], "citation_markers": [marker], "is_quantitative": True}
        if a["kind"] == "association":
            driver_claims.append(claim)
        else:
            results_claims.append(claim)

    # Reference claims (qualitative - corroboration), cited to the reference id.
    lit_claims: list[dict[str, Any]] = []
    for i, r in enumerate(references[:5], start=1):
        marker = f"[L{i}]"
        citations.append(
            {"marker": marker, "ref_id": r["id"], "kind": "reference", "detail": r.get("title")}
        )
        lit_claims.append(
            {
                "text": f'The literature includes "{r["title"]}" ({r["year"]}), '
                f"relevant to these findings.",
                "citation_markers": [marker],
                "is_quantitative": False,
            }
        )

    summary_text = (
        f"This report addresses: {question}. It synthesizes "
        f"{len(analyses)} quantitative results and {len(references)} references. "
        "All numeric claims are grounded in the cited source records."
    )
    methods_text = (
        "Trends were tested with the Mann-Kendall test and Theil-Sen slope; stock "
        "status was classified against MSY reference points (Kobe quadrants); "
        "covariate associations used lagged cross-correlation with a "
        "Bonferroni-adjusted p-value. All statistics are computed by deterministic, "
        "unit-tested functions; associations are not causal."
    )
    limitations = [g.get("detail", "coverage gap") for g in gaps]
    if not driver_claims:
        limitations.append("No environmental-covariate association could be computed.")

    sections = [
        {
            "title": "Summary",
            "claims": [{"text": summary_text, "citation_markers": [], "is_quantitative": False}],
        },
        {
            "title": "Methods",
            "claims": [{"text": methods_text, "citation_markers": [], "is_quantitative": False}],
        },
        {
            "title": "Results",
            "claims": results_claims
            or [
                {
                    "text": "No trend or status results were available.",
                    "citation_markers": [],
                    "is_quantitative": False,
                }
            ],
        },
        {
            "title": "Environmental drivers",
            "claims": driver_claims
            or [
                {
                    "text": "No covariate associations were computed for this run.",
                    "citation_markers": [],
                    "is_quantitative": False,
                }
            ],
        },
        {
            "title": "Literature",
            "claims": lit_claims
            or [
                {
                    "text": "No literature was retrieved.",
                    "citation_markers": [],
                    "is_quantitative": False,
                }
            ],
        },
        {
            "title": "Limitations",
            "claims": [
                {"text": lim, "citation_markers": [], "is_quantitative": False}
                for lim in limitations
            ]
            or [
                {
                    "text": "No major limitations identified.",
                    "citation_markers": [],
                    "is_quantitative": False,
                }
            ],
        },
    ]

    return {
        "run_id": run_id,
        "question": question,
        "sections": sections,
        "figures": figures,
        "citations": citations,
        "limitations": limitations,
        "revision": 0,
    }


def _criticize(context: dict[str, Any]) -> dict[str, Any]:
    # The authoritative grounding check runs in the Critic agent itself; the
    # deterministic "model" contributes no additional notes.
    mechanical = context.get("mechanical_findings", [])
    verdict = (
        "revise" if any(n.get("severity") in {"blocker", "major"} for n in mechanical) else "pass"
    )
    return {"verdict": verdict, "round": 0, "notes": []}


def make_offline_llm(model: str = "deterministic", **kwargs: Any) -> LLMClient:
    """Convenience: an :class:`~fra.llm.LLMClient` wired to the offline backend."""
    return LLMClient(DeterministicBackend(), model=model, **kwargs)


# static type check aid
_backend: LLMBackend = DeterministicBackend()

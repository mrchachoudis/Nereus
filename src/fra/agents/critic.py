"""Critic agent: adversarial review with a mechanical grounding check.

The mechanical check is authoritative and deterministic (DESIGN_PROMPT §7): every
citation marker in the draft must map to a citation whose ``ref_id`` exists on the
Blackboard, and every quantitative claim must carry at least one marker. These
produce blocker notes regardless of what the LLM says. The LLM adds stylistic and
interpretive notes (overconfidence, missing uncertainty, unit issues) on top.
"""

from __future__ import annotations

from typing import Literal

from fra.agents.base import BaseAgent, GuardResult
from fra.blackboard import Blackboard
from fra.llm import LLMClient, LLMError, agent_system, agent_user, load_prompt
from fra.models import CriticNote, CriticVerdict, DraftReport


class CriticAgent(BaseAgent):
    name = "critic"

    def __init__(self, llm: LLMClient, *, timeout_s: float = 180.0) -> None:
        super().__init__(timeout_s=timeout_s, max_retries=1)
        self._llm = llm
        self._template = load_prompt("critic")

    def _guard(self, bb: Blackboard) -> GuardResult:
        if bb.draft is None:
            return GuardResult(ok=False, missing=["draft report"])
        return GuardResult(ok=True)

    async def _run(self, bb: Blackboard) -> Blackboard:
        assert bb.draft is not None
        mechanical = mechanical_check(bb.draft, bb.known_ids())
        llm_notes = self._llm_notes(bb, mechanical)

        notes = mechanical + llm_notes
        verdict_label: Literal["pass", "revise"] = (
            "revise" if any(n.severity in {"blocker", "major"} for n in notes) else "pass"
        )
        verdict = CriticVerdict(verdict=verdict_label, notes=notes, round=len(bb.critic_notes))
        bb.critic_notes = notes
        self.log.info(
            "critic verdict=%s (%d mechanical, %d llm notes)",
            verdict.verdict,
            len(mechanical),
            len(llm_notes),
        )
        # stash the verdict so the orchestrator can read it without re-deriving
        bb.draft.revision = verdict.round
        self._last_verdict = verdict
        return bb

    @property
    def last_verdict(self) -> CriticVerdict:
        return getattr(self, "_last_verdict", CriticVerdict(verdict="pass"))

    def _llm_notes(self, bb: Blackboard, mechanical: list[CriticNote]) -> list[CriticNote]:
        assert bb.draft is not None
        context = {
            "draft": bb.draft.model_dump(mode="json"),
            "known_ids": sorted(bb.known_ids()),
            "mechanical_findings": [n.model_dump(mode="json") for n in mechanical],
        }
        try:
            verdict = self._llm.structured(
                system=agent_system(self.name, self._template),
                user=agent_user(
                    "Review the draft. Incorporate the mechanical_findings and add any "
                    "further defects. Return your verdict and notes.",
                    context,
                ),
                schema=CriticVerdict,
                agent=self.name,
            )
            # Keep only the LLM's *new* notes; mechanical ones are added by us.
            mech_msgs = {n.message for n in mechanical}
            return [n for n in verdict.notes if n.message not in mech_msgs]
        except LLMError as exc:
            self.log.warning("critic LLM review unavailable (%s); relying on mechanical check", exc)
            return []


def mechanical_check(draft: DraftReport, known_ids: set[str]) -> list[CriticNote]:
    """Deterministic grounding verification. Pure function, unit-testable."""
    notes: list[CriticNote] = []
    marker_to_ref = {c.marker: c.ref_id for c in draft.citations}

    # 1. Dangling citations: a declared citation pointing at an unknown ID.
    for citation in draft.citations:
        if citation.ref_id not in known_ids:
            notes.append(
                CriticNote(
                    severity="blocker",
                    category="dangling_citation",
                    message=(
                        f"Citation {citation.marker} references ref_id "
                        f"'{citation.ref_id}', which is not present on the Blackboard."
                    ),
                )
            )

    # 2. Uncited quantitative claims, and markers with no declared citation.
    for section in draft.sections:
        for claim in section.claims:
            if claim.is_quantitative and not claim.citation_markers:
                notes.append(
                    CriticNote(
                        severity="blocker",
                        category="unsupported_claim",
                        section_title=section.title,
                        claim_text=claim.text,
                        message="Quantitative claim carries no citation marker.",
                    )
                )
            for marker in claim.citation_markers:
                if marker not in marker_to_ref:
                    notes.append(
                        CriticNote(
                            severity="major",
                            category="dangling_citation",
                            section_title=section.title,
                            claim_text=claim.text,
                            message=f"Marker {marker} is used but not declared in citations.",
                        )
                    )
    return notes

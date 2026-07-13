"""Thin Anthropic Messages API wrapper (DESIGN_PROMPT §7).

Responsibilities:
  * one place that knows the model string (configurable via env/config, never
    hardcoded deep in agent code);
  * structured-output helper that requests JSON, validates it against a pydantic
    model, and on parse failure retries *once* with the validation error
    appended, then fails loud;
  * token accounting surfaced to the caller for the run log.

The backend is injectable (:class:`LLMBackend`) so agents run under a scripted
mock in tests with no key and no network.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class LLMError(RuntimeError):
    """Raised when the model cannot produce valid output after the retry."""


@dataclass
class LLMResult:
    """A single completion plus token accounting."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str


class LLMBackend(Protocol):
    """Minimal completion interface an LLM provider must satisfy."""

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> LLMResult: ...


class AnthropicBackend:
    """Real backend over the Anthropic SDK. Imported lazily so tests need no key."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 120.0) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("anthropic package not installed") from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY not set")
        self._client = anthropic.Anthropic(api_key=key, timeout=timeout)

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> LLMResult:
        payload = [{"role": m.role, "content": m.content} for m in messages]
        resp = self._client.messages.create(
            model=model,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=payload,  # type: ignore[arg-type]
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return LLMResult(
            text=text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=model,
        )


@dataclass
class TokenLedger:
    """Accumulates token usage across a run for the run log."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    per_agent: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, agent: str, result: LLMResult) -> None:
        self.calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        slot = self.per_agent.setdefault(agent, {"calls": 0, "input": 0, "output": 0})
        slot["calls"] += 1
        slot["input"] += result.input_tokens
        slot["output"] += result.output_tokens


class LLMClient:
    """High-level client used by agents."""

    def __init__(
        self,
        backend: LLMBackend,
        *,
        model: str = "claude-sonnet-5",
        max_tokens: int = 4096,
        temperature: float = 0.2,
        ledger: TokenLedger | None = None,
    ) -> None:
        self._backend = backend
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self.ledger = ledger or TokenLedger()

    def complete(
        self,
        *,
        system: str,
        user: str,
        agent: str = "unknown",
        max_tokens: int | None = None,
    ) -> str:
        result = self._backend.create(
            model=self._model,
            system=system,
            messages=[Message(role="user", content=user)],
            max_tokens=max_tokens or self._max_tokens,
            temperature=self._temperature,
        )
        self.ledger.add(agent, result)
        return result.text

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[M],
        agent: str = "unknown",
    ) -> M:
        """Return a validated ``schema`` instance from a JSON completion.

        On validation failure, retry exactly once with the error appended to the
        prompt (per §7), then raise :class:`LLMError`.
        """
        messages = [Message(role="user", content=user)]
        last_error = ""
        for attempt in range(2):
            result = self._backend.create(
                model=self._model,
                system=system + "\n\nRespond with a single JSON object and nothing else.",
                messages=messages,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
            self.ledger.add(agent, result)
            try:
                payload = _extract_json(result.text)
                return schema.model_validate(payload)
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    "%s structured parse failed (attempt %d): %s", agent, attempt + 1, exc
                )
                messages = [
                    Message(role="user", content=user),
                    Message(role="assistant", content=result.text),
                    Message(
                        role="user",
                        content=(
                            "That did not validate against the required schema. "
                            f"Error:\n{last_error}\n\nReturn corrected JSON only."
                        ),
                    ),
                ]
        raise LLMError(f"{agent}: could not produce valid {schema.__name__}: {last_error}")


def _extract_json(text: str) -> object:
    """Pull the first JSON object/array out of a model response.

    Tolerates fenced code blocks and leading prose; fails loud if nothing
    JSON-shaped is present.
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    start = min(
        (i for i in (candidate.find("{"), candidate.find("[")) if i != -1),
        default=-1,
    )
    if start == -1:
        raise ValueError(f"no JSON found in model output: {text[:200]!r}")
    return json.loads(candidate[start:])


def load_prompt(name: str) -> str:
    """Load a versioned prompt template from ``prompts/`` (DESIGN_PROMPT §7)."""
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


# -- shared prompt convention ------------------------------------------------
# Agents tag the system prompt with their name and append a machine-readable
# context block to the user prompt. The offline/deterministic backend keys on
# both; the real model simply reads them as content.

AGENT_MARKER = "FRA-AGENT:"
CONTEXT_DELIM = "===CONTEXT_JSON==="


def agent_system(name: str, template: str) -> str:
    return f"{AGENT_MARKER} {name}\n\n{template}"


def agent_user(instructions: str, context: object) -> str:
    return f"{instructions}\n\n{CONTEXT_DELIM}\n{json.dumps(context, default=str)}"


def parse_agent_name(system: str) -> str | None:
    for line in system.splitlines():
        if line.startswith(AGENT_MARKER):
            return line[len(AGENT_MARKER) :].strip()
    return None


def parse_context(user: str) -> object:
    if CONTEXT_DELIM in user:
        return json.loads(user.split(CONTEXT_DELIM, 1)[1].strip())
    return {}

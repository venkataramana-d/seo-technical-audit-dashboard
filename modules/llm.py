"""LLM client for the AI Agent subsystems - 09-AI-AGENT-SUBSYSTEMS.md §5.

A thin provider-agnostic interface so agents (QA, Content, ...) can be unit-
tested against a FakeLLM with no network. The real client uses the Anthropic
Messages API with the org's vaulted key (Phase 5). The `anthropic` package is
imported lazily inside the real client, so this module - and the whole worker -
imports fine without it; only a live agent run with a configured key needs it.

Model policy (per the claude-api guidance): default to `claude-opus-4-8`.
Callers may pass a cheaper model (e.g. Haiku) for high-volume, low-stakes work
like QA sampling; keep the strong default for user-facing Content Agent drafts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

DEFAULT_MODEL = "claude-opus-4-8"


@dataclass
class LLMResponse:
    text: str
    model: str


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, max_tokens: int = 1024,
                 model: str | None = None) -> LLMResponse: ...


class FakeLLM:
    """Deterministic test double. `responder(system, user) -> str`."""

    def __init__(self, responder: Callable[[str, str], str], model: str = "fake-model"):
        self._responder = responder
        self._model = model
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 1024,
                 model: str | None = None) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(text=self._responder(system, user), model=model or self._model)


class AnthropicLLM:
    """Real client - Anthropic Messages API. `anthropic` imported lazily."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, max_tokens: int = 1024):
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client = None

    def _ensure(self):
        if self._client is None:
            import anthropic  # lazy: only needed for a real agent run
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 model: str | None = None) -> LLMResponse:
        client = self._ensure()
        resp = client.messages.create(
            model=model or self._model,
            max_tokens=max_tokens or self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
        return LLMResponse(text=text, model=resp.model)


def get_llm_for_org(session, org_id, *, model: str = DEFAULT_MODEL) -> LLMClient | None:
    """Build an LLM client from the org's vaulted Anthropic key, or None if the
    org hasn't stored one (agents that need an LLM then no-op gracefully)."""
    from worker.api_key_service import get_api_key

    key = get_api_key(session, org_id, "anthropic")
    if not key:
        return None
    return AnthropicLLM(api_key=key, model=model)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict | None:
    """Tolerantly extract the first JSON object from an LLM response (models
    sometimes wrap JSON in prose or code fences). Returns None on failure -
    callers must treat a None as 'no usable answer', never crash the crawl."""
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None

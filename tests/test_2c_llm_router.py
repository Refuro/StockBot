"""Ticket 2c — LLM router: spend caps, token logging, tier availability."""

from __future__ import annotations

import pytest

from stockbot.core.llm import (
    LLMRouter,
    LLMSpendCapExceeded,
    ModelTier,
    SpendTracker,
    StubLLMClient,
    build_router,
    parse_json_object,
)
from stockbot.core.llm.router import estimate_cost_usd


def test_cost_estimate_uses_model_table_and_safe_fallback() -> None:
    haiku = estimate_cost_usd("claude-haiku-4-5-20251001", 1_000_000, 0)
    assert haiku == pytest.approx(1.00)
    unknown = estimate_cost_usd("mystery-model", 1_000_000, 0)
    assert unknown >= 15.00  # unknown models priced at the most expensive row


def test_spend_tracker_raises_once_cap_is_reached() -> None:
    tracker = SpendTracker(cap_usd=0.0001, label="test")
    tracker.check()  # nothing spent yet — fine
    tracker.add("claude-opus-4-8", 10_000, 1_000)
    with pytest.raises(LLMSpendCapExceeded):
        tracker.check()


def test_router_charges_tracker_and_blocks_over_cap() -> None:
    stub = StubLLMClient("claude-opus-4-8", default='{"x": 1}')
    router = LLMRouter({ModelTier.STRONG: stub}, SpendTracker(cap_usd=0.001, label="t"))
    router.complete(ModelTier.STRONG, "long prompt " * 200)
    assert router.tracker.calls == 1
    assert router.tracker.spent_usd > 0
    with pytest.raises(LLMSpendCapExceeded):
        router.complete(ModelTier.STRONG, "another " * 200)
    assert router.tracker.calls == 1  # the blocked call never reached the model


def test_build_router_tiers_follow_available_keys(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-a")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    router = build_router(cap_usd=1.0)
    assert router.available(ModelTier.TRIAGE) is True
    assert router.available(ModelTier.STRONG) is True
    assert router.available(ModelTier.CROSS_CHECK) is False  # OpenAI unfunded/no key


def test_parse_json_object_handles_fences_and_garbage() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! Here you go: {"a": 1} hope that helps') == {"a": 1}
    assert parse_json_object("no json here") is None
    assert parse_json_object("[1, 2]") is None  # object required


class _FakeOpenAIModule:
    """Captures the kwargs OpenAILLMClient sends (2c-F1 regression guard)."""

    last_kwargs: dict = {}

    class OpenAI:  # noqa: D106 - mimics openai.OpenAI
        def __init__(self, api_key: str) -> None:
            self.chat = _FakeChat()


class _FakeChat:
    def __init__(self) -> None:
        self.completions = self

    def create(self, **kwargs):
        _FakeOpenAIModule.last_kwargs = kwargs

        class _Msg:
            content = '{"quality_score": 0.5, "probability_drift_up": 0.5}'

        class _Choice:
            message = _Msg()
            finish_reason = "stop"

        class _Usage:
            prompt_tokens = 100
            completion_tokens = 50

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        return _Resp()


def test_openai_client_adds_reasoning_headroom_and_json_mode(monkeypatch) -> None:
    """2c-F1: gpt-5.x reasoning tokens spend the completion budget first — the
    client must pad the ceiling and request JSON mode, else responses truncate
    mid-JSON and the ensemble silently degrades (observed live: 9/11 cut at 700)."""
    import sys

    from stockbot.core.llm.router import REASONING_HEADROOM_TOKENS, OpenAILLMClient

    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIModule)
    client = OpenAILLMClient("gpt-5.5", api_key="k")

    resp = client.complete("Score it. Respond ONLY with JSON.", system="sys", max_tokens=700)
    sent = _FakeOpenAIModule.last_kwargs
    assert sent["max_completion_tokens"] == 700 + REASONING_HEADROOM_TOKENS
    assert sent["response_format"] == {"type": "json_object"}
    assert resp.text.startswith('{"quality_score"')

    client.complete("no structured output requested here", max_tokens=100)
    assert "response_format" not in _FakeOpenAIModule.last_kwargs  # guard: JSON mode only when asked

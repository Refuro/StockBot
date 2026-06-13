"""Shot-2 OPS: Flow Lens daily run uses only providers with keys (no stub poisoning)."""

from __future__ import annotations

from datetime import date

import pytest

from stockbot.config import AppConfig, EnvSettings, load_config
from stockbot.core.state.store import StateStore
from stockbot.tools.flow_lens import logger as fl_logger
from stockbot.tools.flow_lens.prompts import RETAIL_PROMPT_ARCHETYPES
from stockbot.tools.flow_lens.providers import (
    StubConsumerClient,
    available_providers,
    estimate_cost_usd,
)


def test_available_providers_reflects_keys() -> None:
    assert available_providers(EnvSettings(openai_api_key="k", anthropic_api_key="")) == {"openai"}
    assert available_providers(EnvSettings(openai_api_key="", anthropic_api_key="k")) == {"anthropic"}
    assert available_providers(EnvSettings(openai_api_key="", anthropic_api_key="")) == set()
    # Grok/Gemini have no client implementation: never reported available.
    assert "xai" not in available_providers(EnvSettings(openai_api_key="k", anthropic_api_key="k"))


def test_estimate_cost_is_token_proportional() -> None:
    assert estimate_cost_usd("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
    assert estimate_cost_usd("claude-haiku-4-5", 0, 1_000_000) == pytest.approx(5.0)
    assert estimate_cost_usd("unknown-model", 0, 0) == 0.0


def test_live_run_skips_providers_without_keys(tmp_path, monkeypatch) -> None:
    """Live mode must SKIP unavailable providers — never write stub data to the DB."""
    db = tmp_path / "flow_lens.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})

    # Pretend only Anthropic has a key, and intercept the client so no network
    # call happens (the stub stands in for the real Anthropic client here).
    monkeypatch.setattr(fl_logger, "available_providers", lambda: {"anthropic"})
    monkeypatch.setattr(
        fl_logger, "build_consumer_client", lambda offline=False: StubConsumerClient()
    )

    result = fl_logger.run_daily_flow_lens(cfg, logged_date=date(2026, 6, 11), offline=False)

    assert result.models_run == ("claude",)
    assert set(result.models_skipped) == {"chatgpt", "grok", "gemini"}
    assert result.samples_written == len(RETAIL_PROMPT_ARCHETYPES)  # one model only

    store = StateStore(db)
    samples = store.get_flow_lens_samples("2026-06-11")
    assert {s["model_id"] for s in samples} == {"claude"}


def test_provider_failing_mid_run_is_dropped_not_partially_persisted(tmp_path, monkeypatch) -> None:
    """A provider that errors mid-run (quota/outage) contributes NO samples;
    the others still complete and consensus is computed over survivors only."""
    db = tmp_path / "flow_lens.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})

    class _QuotaExhaustedOnChatgpt(StubConsumerClient):
        def complete(self, spec, prompt):
            if spec.model_id == "chatgpt":
                raise RuntimeError("429 insufficient_quota")
            return super().complete(spec, prompt)

    monkeypatch.setattr(fl_logger, "available_providers", lambda: {"openai", "anthropic"})
    monkeypatch.setattr(
        fl_logger, "build_consumer_client", lambda offline=False: _QuotaExhaustedOnChatgpt()
    )

    result = fl_logger.run_daily_flow_lens(cfg, logged_date=date(2026, 6, 11), offline=False)

    assert result.models_run == ("claude",)
    assert result.models_failed == ("chatgpt",)
    assert result.samples_written == len(RETAIL_PROMPT_ARCHETYPES)

    store = StateStore(db)
    samples = store.get_flow_lens_samples("2026-06-11")
    assert {s["model_id"] for s in samples} == {"claude"}  # no partial chatgpt rows


def test_all_providers_failing_raises(tmp_path, monkeypatch) -> None:
    db = tmp_path / "flow_lens.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})

    class _AlwaysFails(StubConsumerClient):
        def complete(self, spec, prompt):
            raise RuntimeError("boom")

    monkeypatch.setattr(fl_logger, "available_providers", lambda: {"openai", "anthropic"})
    monkeypatch.setattr(fl_logger, "build_consumer_client", lambda offline=False: _AlwaysFails())

    with pytest.raises(RuntimeError, match="all flow lens providers failed"):
        fl_logger.run_daily_flow_lens(cfg, logged_date=date(2026, 6, 11), offline=False)


def test_injected_client_path_is_unchanged(tmp_path) -> None:
    """Tests/backfills that inject a client still sample every configured model."""
    db = tmp_path / "flow_lens.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})
    result = fl_logger.run_daily_flow_lens(
        cfg, logged_date=date(2026, 6, 11), client=StubConsumerClient(), offline=True
    )
    assert result.samples_written == len(RETAIL_PROMPT_ARCHETYPES) * 4
    assert result.models_skipped == ()

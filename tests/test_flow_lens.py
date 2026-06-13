"""Tests for Workstream F1 — Flow Lens logger."""

from __future__ import annotations

from datetime import date

from stockbot.config import AppConfig, load_config
from stockbot.core.state.store import StateStore
from stockbot.tools.flow_lens.consensus import compute_daily_consensus
from stockbot.tools.flow_lens.extract import extract_tickers
from stockbot.tools.flow_lens.logger import run_daily_flow_lens
from stockbot.tools.flow_lens.prompts import RETAIL_PROMPT_ARCHETYPES
from stockbot.tools.flow_lens.providers import StubConsumerClient


def test_extract_tickers_from_retail_style_text():
    text = "Retail is buzzing about $AAPL and MSFT; also watch NVDA for AI."
    assert extract_tickers(text) == ["AAPL", "MSFT", "NVDA"]


def test_consensus_and_novelty_flags():
    samples = [
        {"model_id": "chatgpt", "prompt_id": "p1", "tickers": ["NVDA", "AAPL"]},
        {"model_id": "claude", "prompt_id": "p1", "tickers": ["NVDA"]},
        {"model_id": "grok", "prompt_id": "p1", "tickers": ["TSLA"]},
        {"model_id": "gemini", "prompt_id": "p1", "tickers": ["NVDA"]},
    ]
    rows = compute_daily_consensus(
        samples,
        total_models=4,
        prior_consensus_symbols={"AAPL"},
        consensus_threshold=0.5,
    )
    by_sym = {r.symbol: r for r in rows}
    assert by_sym["NVDA"].consensus_score == 0.75
    assert by_sym["NVDA"].novelty_flag is True
    assert by_sym["AAPL"].consensus_score == 0.25
    assert by_sym["AAPL"].novelty_flag is False


def test_daily_run_persists_samples_and_consensus(tmp_path):
    db = tmp_path / "flow_lens.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})
    day = date(2026, 6, 10)

    result = run_daily_flow_lens(cfg, logged_date=day, client=StubConsumerClient(), offline=True)
    expected_samples = len(RETAIL_PROMPT_ARCHETYPES) * 4
    assert result.samples_written == expected_samples
    assert result.consensus_rows >= 1

    store = StateStore(db)
    samples = store.get_flow_lens_samples("2026-06-10")
    assert len(samples) == expected_samples
    assert all(s["model_version"] for s in samples)

    consensus = store.get_flow_lens_consensus("2026-06-10")
    assert consensus
    assert all("consensus_score" in row for row in consensus)
    assert any(row["novelty_flag"] for row in consensus)


def test_novelty_requires_prior_day_consensus(tmp_path):
    db = tmp_path / "flow_lens2.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})
    store = StateStore(db)

    run_daily_flow_lens(cfg, logged_date=date(2026, 6, 9), client=StubConsumerClient(), offline=True)
    day1 = {r["symbol"] for r in store.get_flow_lens_consensus("2026-06-09") if r["novelty_flag"]}

    run_daily_flow_lens(cfg, logged_date=date(2026, 6, 10), client=StubConsumerClient(), offline=True)
    day2_rows = store.get_flow_lens_consensus("2026-06-10")
    repeat_novel = {r["symbol"] for r in day2_rows if r["novelty_flag"] and r["symbol"] in day1}
    assert not repeat_novel

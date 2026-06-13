"""Ticket 2a — standardized surprise + earnings watcher (mechanical, no LLM)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from stockbot.bots.proportionality.surprise import standardized_surprise
from stockbot.bots.proportionality.watcher import WatcherStats, extract_earnings_surprises
from stockbot.data.real_loader import (
    ALPHAVANTAGE_EARNINGS_URL,
    CachedHTTP,
    alpha_vantage_earnings_payload_valid,
    alpha_vantage_payload_is_throttle,
    cache_file_path,
)
from stockbot.types import MarketEvent


def _event(event_id: str, payload: dict, event_type: str = "earnings", symbol: str = "ACME") -> MarketEvent:
    return MarketEvent(
        event_id=event_id,
        event_type=event_type,
        symbol=symbol,
        as_of=datetime(2024, 5, 1, 21, 30, tzinfo=timezone.utc),
        payload=payload,
    )


class TestStandardizedSurprise:
    """Hand-computed values — the 2a acceptance criterion."""

    def test_hand_computed_beat(self) -> None:
        # actual 2.20 vs consensus 2.00 -> (2.20-2.00)/|2.00| = +10%
        assert standardized_surprise(2.20, 2.00) == pytest.approx(0.10)

    def test_hand_computed_miss(self) -> None:
        # actual 0.92 vs consensus 1.00 -> -8%
        assert standardized_surprise(0.92, 1.00) == pytest.approx(-0.08)

    def test_negative_consensus_uses_absolute_denominator(self) -> None:
        # Loss quarter: actual -0.50 vs consensus -1.00 -> (+0.50)/|-1.00| = +50% beat
        assert standardized_surprise(-0.50, -1.00) == pytest.approx(0.50)

    def test_worse_loss_is_negative_surprise(self) -> None:
        # actual -1.25 vs consensus -1.00 -> (-0.25)/1.00 = -25%
        assert standardized_surprise(-1.25, -1.00) == pytest.approx(-0.25)

    def test_zero_consensus_is_undefined_not_a_fake_constant(self) -> None:
        assert standardized_surprise(0.50, 0.0) is None

    def test_non_finite_inputs_are_undefined(self) -> None:
        assert standardized_surprise(float("nan"), 1.0) is None
        assert standardized_surprise(1.0, float("inf")) is None


class TestEarningsWatcher:
    def test_watcher_recomputes_surprise_from_actual_and_consensus(self) -> None:
        stats = WatcherStats()
        events = [
            _event(
                "e1",
                {"actual_eps": 2.20, "consensus_eps": 2.00, "surprise_pct": 0.10},
            ),
            # Payload lies about its surprise: recomputed value must win.
            _event(
                "e2",
                {"actual_eps": 0.92, "consensus_eps": 1.00, "surprise_pct": 0.99},
            ),
        ]
        out = extract_earnings_surprises(events, stats)
        assert [s.surprise_pct for s in out] == [pytest.approx(0.10), pytest.approx(-0.08)]
        assert stats.accepted == 2
        assert stats.mismatched_payload_surprise == 1

    def test_watcher_drops_malformed_events_instead_of_patching(self) -> None:
        stats = WatcherStats()
        events = [
            _event("ok", {"actual_eps": 1.10, "consensus_eps": 1.00}),
            _event("missing", {"surprise_pct": 0.06}),  # the old fake-constant shape
            _event("zero_consensus", {"actual_eps": 0.10, "consensus_eps": 0.0}),
            _event("not_earnings", {"actual_eps": 1.0, "consensus_eps": 1.0}, event_type="filing_8k"),
        ]
        out = extract_earnings_surprises(events, stats)
        assert [s.event_id for s in out] == ["ok"]
        assert stats.seen == 4
        assert stats.missing_inputs == 1
        assert stats.undefined_surprise == 1
        assert stats.not_earnings == 1


class TestAlphaVantageCacheGuard:
    """Rate-limit notes must never poison the resumable ingestion cache."""

    def test_throttle_payloads_are_classified(self) -> None:
        assert alpha_vantage_payload_is_throttle({"Information": "25 requests/day exceeded"})
        assert alpha_vantage_payload_is_throttle({"Note": "thank you"})
        assert not alpha_vantage_payload_is_throttle({})  # delisted/no-data, not throttle
        assert not alpha_vantage_payload_is_throttle({"quarterlyEarnings": []})

    def test_poisoned_cache_entry_is_detected_and_healed(self, tmp_path) -> None:
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        params = {"function": "EARNINGS", "symbol": "ACME", "apikey": "k"}
        poisoned = cache_file_path(tmp_path, ALPHAVANTAGE_EARNINGS_URL, params)
        poisoned.write_text(json.dumps({"Information": "rate limited"}), encoding="utf-8")

        assert not client.has_cached(
            ALPHAVANTAGE_EARNINGS_URL, params=params, validate=alpha_vantage_earnings_payload_valid
        )
        # Offline + invalid cache: entry is deleted (healed) and a miss is raised,
        # so the next online run refetches instead of consuming the poison.
        with pytest.raises(FileNotFoundError):
            client.get_json(
                ALPHAVANTAGE_EARNINGS_URL,
                params=params,
                validate=alpha_vantage_earnings_payload_valid,
            )
        assert not poisoned.exists()

    def test_valid_cache_entry_is_served(self, tmp_path) -> None:
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        params = {"function": "EARNINGS", "symbol": "ACME", "apikey": "k"}
        good = cache_file_path(tmp_path, ALPHAVANTAGE_EARNINGS_URL, params)
        good.write_text(json.dumps({"quarterlyEarnings": []}), encoding="utf-8")
        data = client.get_json(
            ALPHAVANTAGE_EARNINGS_URL, params=params, validate=alpha_vantage_earnings_payload_valid
        )
        assert data == {"quarterlyEarnings": []}

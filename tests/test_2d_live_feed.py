"""Ticket 2d — LiveDataFeed: quotes, 8-K polling, cursor semantics (offline)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stockbot.data.live_feed import TIINGO_IEX_URL, LiveDataFeed
from stockbot.data.real_loader import (
    ALPHAVANTAGE_EARNINGS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKERS_URL,
    TIINGO_DAILY_URL,
    CachedHTTP,
    cache_file_path,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 10, 22, 0, tzinfo=UTC)  # after the close, run day
CIK = 1234567


def _seed(cache_dir: Path) -> None:
    def put(url: str, payload, params=None) -> None:
        cache_file_path(cache_dir, url, params).write_text(json.dumps(payload), encoding="utf-8")

    put(SEC_TICKERS_URL, {"0": {"cik_str": CIK, "ticker": "LIVE", "title": "Live Test Corp"}})
    put(
        SEC_SUBMISSIONS_URL.format(cik=CIK),
        {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-Q", "8-K"],
                    "accessionNumber": [
                        "0000000000-26-000111",
                        "0000000000-26-000099",
                        "0000000000-26-000042",
                    ],
                    "filingDate": ["2026-06-09", "2026-05-01", "2026-02-10"],
                    "acceptanceDateTime": [
                        "2026-06-09T21:05:00.000Z",
                        "2026-05-01T20:00:00.000Z",
                        "2026-02-10T21:10:00.000Z",
                    ],
                    "items": ["2.02,9.01", "", "2.02"],
                }
            }
        },
    )
    put(
        ALPHAVANTAGE_EARNINGS_URL,
        {
            "quarterlyEarnings": [
                {
                    "fiscalDateEnding": "2026-05-31",
                    "reportedDate": "2026-06-09",
                    "reportedEPS": "1.50",
                    "estimatedEPS": "1.00",
                },
                {
                    "fiscalDateEnding": "2026-01-31",
                    "reportedDate": "2026-02-10",
                    "reportedEPS": "0.90",
                    "estimatedEPS": "1.00",
                },
            ]
        },
        params={"function": "EARNINGS", "symbol": "LIVE", "apikey": "av-key"},
    )
    # No IEX entry -> quote path falls back to EOD daily closes.
    put(
        TIINGO_DAILY_URL.format(ticker="LIVE"),
        [
            {"date": "2026-06-09T00:00:00.000Z", "adjClose": 50.0, "adjVolume": 100000},
            {"date": "2026-06-10T00:00:00.000Z", "adjClose": 52.5, "adjVolume": 120000},
        ],
        params={
            "startDate": "2026-05-31",
            "endDate": "2026-06-10",
            "resampleFreq": "daily",
            "token": "tiingo-key",
        },
    )


def _feed(tmp_path: Path, **kwargs) -> LiveDataFeed:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    _seed(cache_dir)
    return LiveDataFeed(
        symbols=["LIVE"],
        state_path=tmp_path / "feed_state.json",
        tiingo_api_key="tiingo-key",
        alphavantage_api_key="av-key",
        sec_user_agent="test test@example.com",
        request_sleep_s=0.0,
        clock=lambda: NOW,
        client=CachedHTTP(cache_dir=cache_dir, offline=True),
        **kwargs,
    )


def test_quote_falls_back_to_latest_eod_close(tmp_path) -> None:
    feed = _feed(tmp_path)
    quote = feed.get_quote("LIVE")
    assert quote.price == pytest.approx(52.5)
    assert quote.as_of == NOW
    feed.validate_point_in_time(quote.as_of)  # PIT-trivial: now is now


def test_new_8k_becomes_earnings_event_with_canonical_payload(tmp_path) -> None:
    feed = _feed(tmp_path)
    events = feed.get_events()
    # Only the 2026-06-09 8-K is inside the 7-day lookback; the February one is not.
    assert [e.event_id for e in events] == ["LIVE-earnings-0000000000-26-000111"]
    ev = events[0]
    assert ev.event_type == "earnings"
    assert ev.payload["actual_eps"] == 1.5
    assert ev.payload["consensus_eps"] == 1.0
    assert ev.payload["accession_number"] == "0000000000-26-000111"
    assert ev.payload["cik"] == CIK
    assert ev.as_of == datetime(2026, 6, 9, 21, 5, tzinfo=UTC)


def test_cursor_commits_on_advance_and_never_redelivers(tmp_path) -> None:
    feed = _feed(tmp_path)
    first = feed.get_events()
    assert len(first) == 1
    assert feed.advance() is False  # live: one tick per process run

    # New process, same state file: the event must not be delivered again.
    feed2 = _feed(tmp_path)
    assert feed2.get_events() == []

    state = json.loads((tmp_path / "feed_state.json").read_text(encoding="utf-8"))
    assert state["cursor"] == NOW.isoformat()
    assert "LIVE-earnings-0000000000-26-000111" in state["seen_event_ids"]


def test_crash_before_advance_redelivers(tmp_path) -> None:
    feed = _feed(tmp_path)
    assert len(feed.get_events()) == 1
    # No advance() — simulate a crash mid-tick. A fresh feed re-delivers.
    feed2 = _feed(tmp_path)
    assert len(feed2.get_events()) == 1


def test_universe_and_advance_semantics(tmp_path) -> None:
    feed = _feed(tmp_path)
    assert feed.get_universe() == ["LIVE"]
    assert feed.advance() is False

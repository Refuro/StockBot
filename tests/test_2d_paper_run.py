"""Ticket 2d — daily paper runner: two lanes, state across one-tick processes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.proportionality.quality import QualityAssessment
from stockbot.bots.proportionality.ride import RideConfig
from stockbot.core.state.store import StateStore
from stockbot.data.live_feed import LiveDataFeed
from stockbot.data.real_loader import (
    ALPHAVANTAGE_EARNINGS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKERS_URL,
    TIINGO_DAILY_URL,
    CachedHTTP,
    cache_file_path,
)
from stockbot.harness.paper_run import GATED_BOT_ID, MECH_BOT_ID, run_paper_tick

UTC = timezone.utc
DAY1 = datetime(2026, 6, 10, 22, 0, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
CIK = 7654321


def _seed_cache(cache_dir: Path, *, price: float, day: datetime) -> None:
    def put(url: str, payload, params=None) -> None:
        cache_file_path(cache_dir, url, params).write_text(json.dumps(payload), encoding="utf-8")

    put(SEC_TICKERS_URL, {"0": {"cik_str": CIK, "ticker": "PAPR", "title": "Paper Test Corp"}})
    put(
        SEC_SUBMISSIONS_URL.format(cik=CIK),
        {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "accessionNumber": ["0000000000-26-000777"],
                    "filingDate": ["2026-06-09"],
                    "acceptanceDateTime": ["2026-06-09T21:05:00.000Z"],
                    "items": ["2.02"],
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
                }
            ]
        },
        params={"function": "EARNINGS", "symbol": "PAPR", "apikey": "av-key"},
    )
    put(
        TIINGO_DAILY_URL.format(ticker="PAPR"),
        [{"date": day.date().isoformat() + "T00:00:00.000Z", "adjClose": price, "adjVolume": 90000}],
        params={
            "startDate": (day.date() - timedelta(days=10)).isoformat(),
            "endDate": day.date().isoformat(),
            "resampleFreq": "daily",
            "token": "tiingo-key",
        },
    )


def _feed(tmp_path: Path, *, day: datetime, price: float) -> LiveDataFeed:
    cache_dir = tmp_path / f"cache-{day.date().isoformat()}"
    cache_dir.mkdir(exist_ok=True)
    _seed_cache(cache_dir, price=price, day=day)
    return LiveDataFeed(
        symbols=["PAPR"],
        state_path=tmp_path / "feed_state.json",
        tiingo_api_key="tiingo-key",
        alphavantage_api_key="av-key",
        sec_user_agent="test test@example.com",
        request_sleep_s=0.0,
        clock=lambda: day,
        client=CachedHTTP(cache_dir=cache_dir, offline=True),
    )


class _StubGate:
    def __init__(self, probability: float) -> None:
        self._p = probability
        self.calls = 0

    @property
    def spend_summary(self) -> dict:
        return {"label": "stub", "spent_usd": 0.0, "calls": self.calls}

    def __call__(self, ev) -> QualityAssessment:
        self.calls += 1
        return QualityAssessment(
            event_id=ev.event_id, symbol=ev.symbol, scored=True,
            quality_score=0.8, stated_probability=self._p,
            rationale="stub: durable beat",
        )


_RIDE = RideConfig(
    entry_mode="threshold",
    surprise_threshold=0.05,
    exit_rules_enabled=True,
    symbol_allowlist=frozenset({"PAPR"}),
)


def _tick(tmp_path: Path, *, day: datetime, price: float, gate) -> dict:
    return run_paper_tick(
        universe_path=_universe_csv(tmp_path),
        db_path=tmp_path / "paper.db",
        state_dir=tmp_path / "state",
        feed=_feed(tmp_path, day=day, price=price),
        quality_gate=gate,
        ride_config=_RIDE,
    )


def _universe_csv(tmp_path: Path) -> Path:
    path = tmp_path / "universe.csv"
    if not path.exists():
        path.write_text("symbol,sector\nPAPR,Technology\n", encoding="utf-8")
    return path


def test_two_daily_ticks_carry_state_and_race_both_lanes(tmp_path) -> None:
    gate = _StubGate(0.72)

    # Day 1: the 8-K event arrives; both lanes submit next-bar entries.
    summary1 = _tick(tmp_path, day=DAY1, price=50.0, gate=gate)
    assert set(summary1["lanes"]) == {GATED_BOT_ID, MECH_BOT_ID}
    assert gate.calls == 1  # gated lane scored the event; mech lane did not

    sidecar = json.loads((tmp_path / "state" / "paper_strategy_state.json").read_text("utf-8"))
    assert len(sidecar[GATED_BOT_ID]["pending_orders"]) == 1
    assert len(sidecar[MECH_BOT_ID]["pending_orders"]) == 1
    assert sidecar[GATED_BOT_ID]["strategy"]["open"][0]["stated_probability"] == pytest.approx(0.72)
    assert sidecar[MECH_BOT_ID]["strategy"]["open"][0]["stated_probability"] == pytest.approx(0.55)

    # Day 2 (new process): yesterday's pending orders fill at today's price.
    summary2 = _tick(tmp_path, day=DAY2, price=51.0, gate=_StubGate(0.72))
    store = StateStore(tmp_path / "paper.db")
    for bot_id in (GATED_BOT_ID, MECH_BOT_ID):
        fills = store.get_fills(bot_id, "paper")
        assert len(fills) == 1, f"{bot_id}: expected the restored entry to fill"
        assert fills[0]["side"] == "buy"
        assert fills[0]["price"] == pytest.approx(51.0, rel=0.01)  # next-bar, day-2 price
    assert summary2["lanes"][GATED_BOT_ID]["fills"] == 1

    # The event was consumed exactly once: no duplicate theses on day 2.
    with store.transaction() as conn:
        theses = conn.execute("SELECT bot_id, stated_probability FROM theses").fetchall()
    by_bot = {r["bot_id"]: r["stated_probability"] for r in theses}
    assert by_bot[GATED_BOT_ID] == pytest.approx(0.72)
    assert by_bot[MECH_BOT_ID] == pytest.approx(0.55)
    assert len(theses) == 2

    # Sidecar now shows settled state: no pending orders, positions open.
    sidecar2 = json.loads((tmp_path / "state" / "paper_strategy_state.json").read_text("utf-8"))
    assert sidecar2[GATED_BOT_ID]["pending_orders"] == []
    assert len(sidecar2[GATED_BOT_ID]["strategy"]["open"]) == 1


def test_failing_gate_blocks_only_the_gated_lane(tmp_path) -> None:
    class _RejectingGate(_StubGate):
        def __call__(self, ev) -> QualityAssessment:
            self.calls += 1
            return QualityAssessment(
                event_id=ev.event_id, symbol=ev.symbol, scored=True,
                quality_score=0.2, stated_probability=0.30,
                rationale="stub: one-off tax benefit",
            )

    _tick(tmp_path, day=DAY1, price=50.0, gate=_RejectingGate(0.30))
    store = StateStore(tmp_path / "paper.db")
    gated_entries = store.get_decisions(GATED_BOT_ID, "paper", "entry")
    assert [d["action"] for d in gated_entries] == ["skipped_quality_gate"]
    sidecar = json.loads((tmp_path / "state" / "paper_strategy_state.json").read_text("utf-8"))
    assert sidecar[GATED_BOT_ID]["pending_orders"] == []
    assert len(sidecar[MECH_BOT_ID]["pending_orders"]) == 1  # control lane still trades

"""Shot-2 hardening tickets: 2-R1 (stuck-trade edge) + 2-R2 (run-context leak)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.earnings_baseline import BaselineStrategyConfig, EarningsBaselineStrategy
from stockbot.bots.proportionality.ride import (
    BOT_ID,
    MISSING_POSITION_RETRY_TICKS,
    ProportionalityRideStrategy,
    RideConfig,
)
from stockbot.config import AppConfig, SimulatedBrokerConfig
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.harness.loop import drive_strategies, drive_strategy
from stockbot.types import ExecutionMode

UTC = timezone.utc


def _write_dataset(data_dir: Path, days: int = 16) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume"])
        writer.writeheader()
        for day in range(days):
            ts = start + timedelta(days=day)
            writer.writerow(
                {
                    "timestamp": ts.isoformat(),
                    "symbol": "AAA",
                    "price": f"{50 * (1.01 ** day):.4f}",
                    "bid": "",
                    "ask": "",
                    "volume": 200_000,
                }
            )
    with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        writer.writeheader()
        writer.writerow(
            {
                "event_id": "AAA-earnings-1",
                "event_type": "earnings",
                "symbol": "AAA",
                "timestamp": (start + timedelta(days=2, hours=1, minutes=30)).isoformat(),
                "payload_json": json.dumps({"actual_eps": 1.2, "consensus_eps": 1.0, "surprise_pct": 0.2}),
            }
        )


def _cfg(data_dir: Path, db_path: Path) -> AppConfig:
    cfg = AppConfig.model_validate(
        {
            "mode": ExecutionMode.BACKTEST,
            "datafeed": {"backtest": "historical_csv", "paper": "fake_live", "live": "fake_live"},
            "broker": {"backtest": "simulated", "paper": "simulated", "live": "simulated"},
            "historical_data": {
                "prices_path": str(data_dir / "prices.csv"),
                "events_path": str(data_dir / "events.csv"),
            },
            "state": {"db_path": str(db_path)},
        }
    )
    cfg.simulated_broker = SimulatedBrokerConfig(initial_cash=100_000.0, execution_timing="next_bar")
    return cfg


class _PositionVanishesStrategy(ProportionalityRideStrategy):
    """Test double for 2-R1: wipes the broker position once the entry has filled.

    Simulates a live-mode reconciliation surprise — the entry fill is recorded
    in the DB, but the broker no longer holds the position at exit time.
    """

    def __init__(self, config: RideConfig) -> None:
        super().__init__(config)
        self.vanished = False

    def on_tick(self, ctx) -> None:
        if not self.vanished and self._open:
            symbol = next(iter(self._open))
            fills = ctx.store.get_fills(self.bot_id, ctx.mode)
            if any(f["symbol"] == symbol and f["side"] == "buy" for f in fills):
                assert isinstance(ctx.broker, SimulatedBroker)
                ctx.broker._positions.pop(symbol, None)  # noqa: SLF001 - test hook
                self.vanished = True
        super().on_tick(ctx)


class Test2R1StuckTradeEdge:
    def test_filled_entry_with_missing_position_abandons_loudly_after_bounded_retries(
        self, tmp_path
    ) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)
        strategies: list[_PositionVanishesStrategy] = []

        def factory(_comp) -> Strategy:
            strat = _PositionVanishesStrategy(ride)
            strategies.append(strat)
            return strat

        drive_strategy(factory, cfg)
        strat = strategies[0]
        assert strat.vanished, "test double never saw a filled entry"

        store = StateStore(db_path)
        decisions = store.get_decisions(BOT_ID, "backtest", "exit")
        abandoned = [d for d in decisions if d["action"] == "abandoned_position_missing"]
        assert len(abandoned) == 1
        assert abandoned[0]["inputs"]["retries"] == MISSING_POSITION_RETRY_TICKS
        assert abandoned[0]["inputs"]["entry_fill_id"]
        # The trade is dropped — no infinite retry, no sell ever submitted.
        assert "AAA" not in strat._open  # noqa: SLF001
        fills = store.get_fills(BOT_ID, "backtest")
        assert [f["side"] for f in fills] == ["buy"]

    def test_unfilled_entry_still_abandons_via_existing_path(self, tmp_path) -> None:
        """The pre-existing abandoned_no_entry_fill path is unchanged."""
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)

        class _EntryNeverFills(ProportionalityRideStrategy):
            def on_tick(self, ctx) -> None:
                super().on_tick(ctx)
                # Drop pending orders right after submission, before the next
                # tick's settlement — the entry never fills.
                assert isinstance(ctx.broker, SimulatedBroker)
                ctx.broker._pending_orders.clear()  # noqa: SLF001 - test hook

        def factory(_comp) -> Strategy:
            return _EntryNeverFills(ride)

        drive_strategy(factory, cfg)
        store = StateStore(db_path)
        decisions = store.get_decisions(BOT_ID, "backtest", "exit")
        actions = {d["action"] for d in decisions}
        assert "abandoned_no_entry_fill" in actions
        assert "abandoned_position_missing" not in actions


class Test2R2RunContextLeak:
    def test_failed_lane_construction_finishes_started_run_contexts(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)

        def good(_comp) -> Strategy:
            return EarningsBaselineStrategy(BaselineStrategyConfig(), bot_id="lane_zero")

        def bad(_comp) -> Strategy:
            raise RuntimeError("lane construction blew up")

        with pytest.raises(RuntimeError, match="lane construction blew up"):
            drive_strategies([good, bad], cfg)

        store = StateStore(db_path)
        with store.transaction() as conn:
            rows = conn.execute("SELECT bot_id, status FROM runs").fetchall()
        assert len(rows) == 1
        assert rows[0]["bot_id"] == "lane_zero"
        assert rows[0]["status"] == "failed"  # not stranded as "running"

    def test_duplicate_bot_id_also_finishes_first_lane(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)

        def factory(_comp) -> Strategy:
            return EarningsBaselineStrategy(BaselineStrategyConfig(), bot_id="dup")

        with pytest.raises(ValueError, match="Duplicate bot_id"):
            drive_strategies([factory, factory], cfg)

        store = StateStore(db_path)
        with store.transaction() as conn:
            rows = conn.execute("SELECT status FROM runs").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"

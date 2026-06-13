"""Phase 4a (multi-strategy fan-out) + 4b (dashboard data layer)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.earnings_baseline import BaselineStrategyConfig, EarningsBaselineStrategy
from stockbot.config import AppConfig, SimulatedBrokerConfig
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.dashboard.app import render_execution_page, render_page
from stockbot.dashboard.data import (
    calibration_by_strategy_bucket,
    equity_curve,
    list_bot_lanes,
    slippage_summary,
)
from stockbot.harness.paper import run_paper_fanout
from stockbot.types import ExecutionMode

UTC = timezone.utc


def _write_dataset(data_dir: Path, days: int = 12) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 3, 1, 20, 0, tzinfo=UTC)
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
                    "volume": 100_000,
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
                "timestamp": (start + timedelta(days=1, hours=1)).isoformat(),
                "payload_json": json.dumps({"surprise_pct": 0.10, "actual_eps": 1.1, "consensus_eps": 1.0}),
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
    cfg.simulated_broker = SimulatedBrokerConfig(initial_cash=100_000.0, execution_timing="same_bar")
    return cfg


def _factory(bot_id: str, hold_days: int):
    def make(_comp) -> Strategy:
        return EarningsBaselineStrategy(
            BaselineStrategyConfig(surprise_threshold=0.05, hold_days=hold_days), bot_id=bot_id
        )

    return make


class TestFanout:
    def test_two_strategies_share_one_feed_and_separate_ledgers(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)

        results = run_paper_fanout(
            cfg,
            make_strategies=[_factory("variant_fast", 2), _factory("variant_slow", 6)],
        )

        assert set(results) == {"variant_fast", "variant_slow"}
        # Same shared feed: identical step counts and identical time span.
        fast, slow = results["variant_fast"], results["variant_slow"]
        assert fast.steps == slow.steps
        assert fast.start_as_of == slow.start_as_of
        assert fast.end_as_of == slow.end_as_of

        store = StateStore(db_path)
        fast_fills = store.get_fills("variant_fast", "backtest")
        slow_fills = store.get_fills("variant_slow", "backtest")
        # Both entered the same event at the same price (identical feed),
        # but exited on different schedules — the ledger separates them cleanly.
        assert fast_fills[0]["price"] == pytest.approx(slow_fills[0]["price"])
        assert len(fast_fills) == 2 and len(slow_fills) == 2
        assert fast_fills[1]["filled_at"] < slow_fills[1]["filled_at"]

        # Snapshots persisted per bot at the same timestamps.
        fast_curve = store.get_equity_curve("variant_fast", "backtest")
        slow_curve = store.get_equity_curve("variant_slow", "backtest")
        assert [p["as_of"] for p in fast_curve] == [p["as_of"] for p in slow_curve]

    def test_duplicate_bot_ids_are_rejected(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        cfg = _cfg(data_dir, tmp_path / "state.db")
        with pytest.raises(ValueError, match="Duplicate bot_id"):
            run_paper_fanout(cfg, make_strategies=[_factory("same", 2), _factory("same", 3)])


class TestDashboardData:
    def _seeded_db(self, tmp_path) -> Path:
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)
        run_paper_fanout(cfg, make_strategies=[_factory("bot_a", 2), _factory("bot_b", 6)])
        # Seed calibration rows: one calibrated bucket, one not.
        store = StateStore(db_path)
        for i in range(12):
            thesis_id = f"t{i}"
            store.record_thesis(
                thesis_id=thesis_id,
                bot_id="bot_a",
                symbol="AAA",
                direction="long",
                stated_probability=0.9,
                mode="backtest",
                strategy_bucket="earnings:ride",
            )
            store.record_calibration(
                thesis_id=thesis_id,
                bot_id="bot_a",
                trade_type="earnings_ride",
                stated_probability=0.9,
                outcome=True,
                bucket="high_confidence",
                strategy_bucket="earnings:ride",
            )
        return db_path

    def test_lanes_curves_calibration_and_slippage(self, tmp_path) -> None:
        db_path = self._seeded_db(tmp_path)

        lanes = list_bot_lanes(db_path)
        assert {(l.bot_id, l.mode) for l in lanes} == {("bot_a", "backtest"), ("bot_b", "backtest")}

        curve = equity_curve(db_path, "bot_a", "backtest")
        assert len(curve) > 2 and curve[0]["total_equity"] > 0

        cal = calibration_by_strategy_bucket(db_path)
        ride = next(r for r in cal if r["strategy_bucket"] == "earnings:ride")
        assert ride["count"] == 12
        assert ride["calibrated"] is True  # 12 wins at p=0.9 -> brier 0.01

        slip = slippage_summary(db_path, assumed_slippage_pct=0.003)
        assert all(r["fills"] >= 2 for r in slip)
        a = next(r for r in slip if r["bot_id"] == "bot_a")
        assert a["mean_realized_slippage_pct"] is not None
        assert a["realized_vs_assumed"] is not None

    def test_render_page_produces_html(self, tmp_path) -> None:
        db_path = self._seeded_db(tmp_path)
        home = render_page(db_path, assumed_slippage_pct=0.003)
        assert "/strategy/" in home
        exec_page = render_execution_page(db_path, assumed_slippage_pct=0.003)
        assert "Equity curves" in exec_page
        assert "<svg" in exec_page
        assert "realized" in exec_page.lower()

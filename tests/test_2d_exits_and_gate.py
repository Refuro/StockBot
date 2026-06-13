"""Ticket 2d exits + 2c gate wiring: rule-based exits, per-trade probabilities,
quality-gated entries. Default config stays bit-identical to 2b."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.proportionality.quality import QualityAssessment
from stockbot.bots.proportionality.ride import (
    BOT_ID,
    ProportionalityRideStrategy,
    RideConfig,
)
from stockbot.config import AppConfig, SimulatedBrokerConfig
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.harness.loop import drive_strategy
from stockbot.types import ExecutionMode

UTC = timezone.utc
START = datetime(2024, 1, 1, 20, 0, tzinfo=UTC)


def _write_dataset(data_dir: Path, prices: list[float]) -> None:
    """One symbol (AAA), one big positive surprise event after day-2's bar."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume"])
        writer.writeheader()
        for day, price in enumerate(prices):
            writer.writerow(
                {
                    "timestamp": (START + timedelta(days=day)).isoformat(),
                    "symbol": "AAA",
                    "price": f"{price:.4f}",
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
                "timestamp": (START + timedelta(days=2, hours=1, minutes=30)).isoformat(),
                "payload_json": json.dumps({"actual_eps": 1.2, "consensus_eps": 1.0}),
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
    cfg.simulated_broker = SimulatedBrokerConfig(
        initial_cash=100_000.0, execution_timing="next_bar", slippage_pct=0.0,
        cost_profile="flat",
    )
    return cfg


_RULES = RideConfig(
    entry_mode="threshold",
    surprise_threshold=0.05,
    exit_rules_enabled=True,
)


def _run(tmp_path: Path, prices: list[float], ride: RideConfig,
         quality_gate=None) -> StateStore:
    data_dir = tmp_path / "data"
    _write_dataset(data_dir, prices)
    db_path = tmp_path / "state.db"
    cfg = _cfg(data_dir, db_path)

    def factory(_comp) -> Strategy:
        return ProportionalityRideStrategy(ride, quality_gate=quality_gate)

    drive_strategy(factory, cfg)
    return StateStore(db_path)


def _exit_actions(store: StateStore) -> list[str]:
    return [d["action"] for d in store.get_decisions(BOT_ID, "backtest", "exit")]


class TestExitRules:
    def test_profit_take_then_trailing_stop_resolves_on_weighted_exit(self, tmp_path) -> None:
        # fill@100 (day4) -> +16% (profit take 50%) -> high 118 -> -7.6% off high
        prices = [100, 100, 100, 100, 100, 116, 118, 118, 109, 109, 109, 109, 109]
        store = _run(tmp_path, prices, _RULES)
        actions = _exit_actions(store)
        assert "profit_take_partial" in actions
        assert "trailing_stop" in actions

        fills = store.get_fills(BOT_ID, "backtest")
        sells = [f for f in fills if f["side"] == "sell"]
        assert len(sells) == 2  # the partial then the remainder
        assert sells[0]["quantity"] == pytest.approx(sells[1]["quantity"])

        resolutions = store.get_decisions(BOT_ID, "backtest", "resolution")
        assert len(resolutions) == 1
        res = resolutions[0]["inputs"]
        assert res["exit_fills"] == 2
        # Weighted avg of 118 and 109 on equal quantities, entry 100.
        assert res["net_return"] == pytest.approx(0.135, abs=1e-6)
        assert resolutions[0]["action"] == "win"

    def test_hard_stop_cuts_loss(self, tmp_path) -> None:
        prices = [100, 100, 100, 100, 100, 91, 91, 91, 91]
        store = _run(tmp_path, prices, _RULES)
        actions = _exit_actions(store)
        assert "hard_stop" in actions
        assert "trailing_stop" not in actions  # hard stop checked first
        resolutions = store.get_decisions(BOT_ID, "backtest", "resolution")
        assert len(resolutions) == 1
        assert resolutions[0]["action"] == "loss"

    def test_time_stop_after_fourteen_days(self, tmp_path) -> None:
        prices = [100.0] * 21  # flat: no other rule ever triggers
        store = _run(tmp_path, prices, _RULES)
        actions = _exit_actions(store)
        assert actions.count("time_stop") == 1
        assert {"hard_stop", "trailing_stop", "profit_take_partial"}.isdisjoint(actions)
        exits = store.get_decisions(BOT_ID, "backtest", "exit")
        time_stop = next(d for d in exits if d["action"] == "time_stop")
        entry_ts = datetime.fromisoformat(time_stop["inputs"]["entry_ts"])
        as_of = datetime.fromisoformat(time_stop["as_of"])
        assert as_of - entry_ts >= timedelta(days=14)

    def test_default_config_keeps_2b_behaviour(self, tmp_path) -> None:
        """exit_rules_enabled defaults OFF: plain hold_days sell, even on a path
        that would have triggered the profit-take and hard-stop rules."""
        prices = [100, 100, 100, 100, 100, 120, 85, 85, 85, 85, 85, 85, 85, 85, 85]
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)
        store = _run(tmp_path, prices, ride)
        actions = _exit_actions(store)
        assert "sell" in actions
        assert {"profit_take_partial", "hard_stop", "trailing_stop", "time_stop"}.isdisjoint(actions)


def _assessment(prob: float, flags: list[str] | None = None) -> QualityAssessment:
    return QualityAssessment(
        event_id="AAA-earnings-1", symbol="AAA", scored=True,
        quality_score=0.8, stated_probability=prob, flags=flags or [],
        rationale="durable demand-driven beat",
    )


class TestQualityGateWiring:
    def test_passing_gate_sets_thesis_probability(self, tmp_path) -> None:
        prices = [100.0] * 12
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)
        store = _run(tmp_path, prices, ride, quality_gate=lambda ev: _assessment(0.71))

        with store.transaction() as conn:
            theses = conn.execute("SELECT stated_probability, rationale FROM theses").fetchall()
        assert len(theses) == 1
        assert theses[0]["stated_probability"] == pytest.approx(0.71)
        assert "quality:" in theses[0]["rationale"]

        resolutions = store.get_decisions(BOT_ID, "backtest", "resolution")
        assert len(resolutions) == 1
        with store.transaction() as conn:
            cal = conn.execute("SELECT stated_probability FROM calibration_ledger").fetchall()
        assert cal[0]["stated_probability"] == pytest.approx(0.71)

    def test_failing_gate_skips_entry_loudly(self, tmp_path) -> None:
        prices = [100.0] * 8
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)
        store = _run(tmp_path, prices, ride, quality_gate=lambda ev: _assessment(0.40))

        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        assert [d["action"] for d in entries] == ["skipped_quality_gate"]
        assert entries[0]["inputs"]["quality"]["stated_probability"] == pytest.approx(0.40)
        assert store.get_fills(BOT_ID, "backtest") == []

    def test_disagreement_flag_lands_in_rationale(self, tmp_path) -> None:
        prices = [100.0] * 12
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)
        gate = lambda ev: _assessment(0.58, flags=["ensemble_disagreement"])  # noqa: E731
        store = _run(tmp_path, prices, ride, quality_gate=gate)
        with store.transaction() as conn:
            theses = conn.execute("SELECT rationale FROM theses").fetchall()
        assert "ensemble_disagreement" in theses[0]["rationale"]

    def test_no_gate_is_mechanical_constant(self, tmp_path) -> None:
        prices = [100.0] * 12
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)
        store = _run(tmp_path, prices, ride, quality_gate=None)
        with store.transaction() as conn:
            theses = conn.execute("SELECT stated_probability FROM theses").fetchall()
        assert theses[0]["stated_probability"] == pytest.approx(0.55)

"""Ticket 2b — mechanical drift strategy + study runner."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.proportionality.ride import (
    BOT_ID,
    ProportionalityRideStrategy,
    RideConfig,
    percentile_nearest_rank,
)
from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.harness.loop import drive_strategy
from stockbot.research.drift_study import run_drift_study, symbol_dollar_adv
from stockbot.types import ExecutionMode
from stockbot.config import AppConfig, SimulatedBrokerConfig

UTC = timezone.utc


def _write_dataset(
    data_dir: Path,
    *,
    symbols: dict[str, float],
    days: int = 16,
    surprises: dict[str, float] | None = None,
    daily_drift: float = 0.01,
) -> None:
    """Synthetic dataset in the real-loader CSV shape: daily quotes + one earnings event."""
    data_dir.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume", "listed_at", "delisted_at"],
        )
        writer.writeheader()
        for day in range(days):
            ts = start + timedelta(days=day)
            for symbol, base in symbols.items():
                price = base * (1 + daily_drift) ** day
                writer.writerow(
                    {
                        "timestamp": ts.isoformat(),
                        "symbol": symbol,
                        "price": f"{price:.4f}",
                        "bid": "",
                        "ask": "",
                        "volume": 200_000,
                        "listed_at": "",
                        "delisted_at": "",
                    }
                )
    with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        writer.writeheader()
        event_ts = start + timedelta(days=2, hours=1, minutes=30)  # day 2 @ 21:30
        for symbol, surprise in (surprises or {}).items():
            consensus = 1.0
            actual = consensus * (1 + surprise)
            writer.writerow(
                {
                    "event_id": f"{symbol}-earnings-1",
                    "event_type": "earnings",
                    "symbol": symbol,
                    "timestamp": event_ts.isoformat(),
                    "payload_json": json.dumps(
                        {
                            "actual_eps": actual,
                            "consensus_eps": consensus,
                            "surprise_pct": surprise,
                            "source": "test",
                        }
                    ),
                }
            )
    (data_dir / "metadata.json").write_text(
        json.dumps(
            {
                "start": "2024-01-01",
                "end": "2024-01-16",
                "price_provider": "test",
                "event_provider": "test",
                "survivorship_biased": True,
                "rows": {"prices": days * len(symbols), "events": len(surprises or {})},
            }
        ),
        encoding="utf-8",
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
        initial_cash=100_000.0,
        execution_timing="next_bar",
        cost_profile="small_cap",
    )
    return cfg


class TestPercentile:
    def test_nearest_rank(self) -> None:
        values = [float(i) for i in range(1, 101)]  # 1..100
        assert percentile_nearest_rank(values, 0.90) == 90.0
        assert percentile_nearest_rank([5.0], 0.90) == 5.0

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            percentile_nearest_rank([], 0.9)


class TestEntryRule:
    def test_top_decile_requires_history(self) -> None:
        strat = ProportionalityRideStrategy(RideConfig(entry_mode="top_decile", min_history_for_decile=40))
        signal, info = strat._entry_signal(0.50)
        assert not signal
        assert info["reason"] == "insufficient_history"

    def test_top_decile_cutoff_uses_prior_history_only(self) -> None:
        strat = ProportionalityRideStrategy(RideConfig(entry_mode="top_decile", min_history_for_decile=40))
        # 40 prior surprises: 1%..40%; 90th percentile (nearest rank) = 36%
        strat._surprise_history = [i / 100 for i in range(1, 41)]
        signal, info = strat._entry_signal(0.36)
        assert signal and info["cutoff"] == pytest.approx(0.36)
        signal, _ = strat._entry_signal(0.35)
        assert not signal

    def test_negative_surprise_never_enters(self) -> None:
        strat = ProportionalityRideStrategy(RideConfig(entry_mode="threshold", surprise_threshold=0.05))
        signal, info = strat._entry_signal(-0.50)
        assert not signal and info["reason"] == "non_positive_surprise"


class TestRideStrategyEndToEnd:
    def test_ride_trades_tag_bucket_and_resolve_via_ledger(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(
            data_dir,
            symbols={"AAA": 50.0, "BBB": 80.0},
            surprises={"AAA": 0.20, "BBB": 0.01},  # only AAA clears the 5% threshold
        )
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)

        def factory(_comp) -> Strategy:
            return ProportionalityRideStrategy(ride)

        result = drive_strategy(factory, cfg)
        store = StateStore(db_path)
        fills = store.get_fills(BOT_ID, "backtest")

        # One round trip on AAA only; orders went through submit_and_record.
        assert [f["symbol"] for f in fills] == ["AAA", "AAA"]
        assert [f["side"] for f in fills] == ["buy", "sell"]
        assert result.fills == 2

        # next_bar: the entry fill lands strictly after the event timestamp.
        event_ts = datetime(2024, 1, 3, 21, 30, tzinfo=UTC)
        assert fills[0]["filled_at"] > event_ts

        # Thesis + calibration rows carry the conventions from the plan.
        ledger = CalibrationLedger(store)
        bucket = CalibrationLedger.strategy_bucket("earnings", "ride")
        assert bucket == "earnings:ride"
        stats = store.get_calibration_stats(BOT_ID, strategy_bucket=bucket)
        assert stats["count"] == 1  # the AAA round trip resolved
        with store.transaction() as conn:
            thesis_rows = conn.execute(
                "SELECT bot_id, strategy_bucket, outcome FROM theses"
            ).fetchall()
        assert len(thesis_rows) == 1
        assert thesis_rows[0]["bot_id"] == BOT_ID
        assert thesis_rows[0]["strategy_bucket"] == "earnings:ride"
        assert thesis_rows[0]["outcome"] == 1  # upward-drifting synthetic data -> win

        # Decision audit trail: entry, exit, and resolution all recorded.
        types_seen = {d["decision_type"] for d in store.get_decisions(BOT_ID, "backtest")}
        assert {"entry", "exit", "resolution"} <= types_seen
        assert not ledger.is_strategy_bucket_calibrated(BOT_ID, bucket)  # 1 sample < 10

    def test_allowlist_excludes_low_adv_symbols(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(
            data_dir,
            symbols={"AAA": 50.0, "ZZZ": 50.0},
            surprises={"AAA": 0.20, "ZZZ": 0.20},
        )
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)
        ride = RideConfig(
            entry_mode="threshold",
            surprise_threshold=0.05,
            hold_days=3,
            symbol_allowlist=frozenset({"AAA"}),
        )

        def factory(_comp) -> Strategy:
            return ProportionalityRideStrategy(ride)

        drive_strategy(factory, cfg)
        store = StateStore(db_path)
        symbols_traded = {f["symbol"] for f in store.get_fills(BOT_ID, "backtest")}
        assert symbols_traded == {"AAA"}


class TestDriftStudy:
    def test_symbol_dollar_adv(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(data_dir, symbols={"AAA": 50.0}, surprises={})
        adv = symbol_dollar_adv(data_dir / "prices.csv")
        # 200k shares x ~$50 = ~$10M median dollar volume
        assert adv["AAA"] == pytest.approx(50.0 * 200_000, rel=0.2)

    def test_study_writes_report_with_preregistered_table(self, tmp_path) -> None:
        data_dir = tmp_path / "data"
        _write_dataset(
            data_dir,
            symbols={"AAA": 50.0, "BBB": 80.0},
            surprises={"AAA": 0.20, "BBB": 0.08},
        )
        out = tmp_path / "reports" / "2b.md"
        report_path = run_drift_study(
            data_dir=data_dir,
            study_dir=tmp_path / "study",
            out_path=out,
        )
        text = Path(report_path).read_text(encoding="utf-8")
        assert "Pre-registered primary test" in text
        assert "top-decile" in text
        assert "Survivorship" in text
        assert "Reproduction" in text
        # All 9 cells ran, each in its own DB.
        assert len(list((tmp_path / "study").glob("*.db"))) == 9
        # Threshold cells traded on the synthetic beats (decile cells lack history).
        assert "surprise > 5%, 10d hold" in text

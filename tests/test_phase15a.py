"""Phase 1.5a tests — Strategy protocol; one strategy, identical across modes."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stockbot.bots.earnings_baseline import BaselineStrategyConfig, EarningsBaselineStrategy
from stockbot.bots.hello_world import HelloWorldBot
from stockbot.config import AppConfig, ExecutionMode, load_config
from stockbot.core.strategy import Strategy
from stockbot.core.state.store import StateStore
from stockbot.harness.backtest import make_strategy_factory, run_backtest
from stockbot.harness.paper import run_paper
from stockbot.harness.runtime import SpineComponents


def _write_dataset(prices_path: Path, events_path: Path) -> None:
    prices_path.parent.mkdir(parents=True, exist_ok=True)
    start = date(2021, 1, 1)
    end = date(2022, 6, 1)
    day = start
    with prices_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "symbol", "price", "bid", "ask", "volume"])
        i = 0
        while day <= end:
            if day.weekday() < 5:
                price = 50.0 + i * 0.03 + (i % 5) * 0.01
                ts = datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc).isoformat()
                w.writerow([ts, "FAKE", f"{price:.4f}", f"{price-0.01:.4f}", f"{price+0.01:.4f}", 100000 + i])
                i += 1
            day += timedelta(days=1)
    with events_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        ev = date(2021, 1, 15)
        idx = 0
        while ev <= end:
            while ev.weekday() >= 5:
                ev += timedelta(days=1)
            ts = datetime(ev.year, ev.month, ev.day, 14, 30, tzinfo=timezone.utc).isoformat()
            payload = '{"surprise_pct": 0.06, "earnings_date": "' + ev.isoformat() + '"}'
            w.writerow([f"earn-{idx}", "earnings", "FAKE", ts, payload])
            idx += 1
            ev += timedelta(days=90)


def _cfg(tmp_path: Path, mode: ExecutionMode, suffix: str) -> AppConfig:
    prices = tmp_path / f"prices_{suffix}.csv"
    events = tmp_path / f"events_{suffix}.csv"
    _write_dataset(prices, events)
    base = load_config().model_dump()
    # Both modes read the SAME historical feed so decisions are comparable.
    return AppConfig(
        **{
            **base,
            "mode": mode,
            "state": {"db_path": str(tmp_path / f"db_{suffix}.db")},
            "simulated_broker": {"initial_cash": 100_000.0, "slippage_pct": 0.003},
            "datafeed": {
                "backtest": "historical_csv",
                "paper": "historical_csv",
                "live": "historical_csv",
            },
            "historical_data": {"prices_path": str(prices), "events_path": str(events)},
        }
    )


def _write_next_bar_dataset(prices_path: Path, events_path: Path) -> tuple[datetime, datetime]:
    prices_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    t2 = t1 + timedelta(days=1)
    with prices_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "symbol", "price", "bid", "ask", "volume"])
        w.writerow([t0.isoformat(), "FAKE", "100.00", "99.99", "100.01", "100000"])
        w.writerow([t1.isoformat(), "FAKE", "101.00", "100.99", "101.01", "100000"])
        w.writerow([t2.isoformat(), "FAKE", "102.00", "101.99", "102.01", "100000"])
    with events_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        w.writerow(["earn-0", "earnings", "FAKE", t0.isoformat(), '{"surprise_pct": 0.10}'])
    return t0, t1


def _cfg_with_execution_timing(tmp_path: Path, execution_timing: str, suffix: str) -> tuple[AppConfig, datetime, datetime]:
    prices = tmp_path / f"prices_exec_{suffix}.csv"
    events = tmp_path / f"events_exec_{suffix}.csv"
    signal_ts, next_quote_ts = _write_next_bar_dataset(prices, events)
    base = load_config().model_dump()
    cfg = AppConfig(
        **{
            **base,
            "mode": ExecutionMode.BACKTEST,
            "state": {"db_path": str(tmp_path / f"db_exec_{suffix}.db")},
            "simulated_broker": {
                "initial_cash": 100_000.0,
                "slippage_pct": 0.003,
                "execution_timing": execution_timing,
            },
            "datafeed": {
                "backtest": "historical_csv",
                "paper": "historical_csv",
                "live": "historical_csv",
            },
            "historical_data": {"prices_path": str(prices), "events_path": str(events)},
        }
    )
    return cfg, signal_ts, next_quote_ts


def _fills_signature(store: StateStore, bot_id: str, mode: str) -> list[tuple]:
    """Decision fingerprint independent of random fill/order ids."""
    return [
        (f["symbol"], f["side"], f["quantity"], round(f["price"], 6), f["filled_at"].isoformat())
        for f in store.get_fills(bot_id, mode)
    ]


def test_helloworld_and_baseline_are_strategies():
    baseline = EarningsBaselineStrategy(BaselineStrategyConfig())
    assert isinstance(baseline, Strategy)
    assert baseline.bot_id == "hello_world"
    assert hasattr(baseline, "on_tick")
    # HelloWorldBot also implements the Strategy protocol.
    assert issubclass(HelloWorldBot, Strategy)


def test_same_strategy_identical_decisions_backtest_vs_paper(tmp_path: Path):
    """One EarningsBaselineStrategy class, run unchanged under backtest and paper,
    produces identical decisions on identical data."""
    baseline_cfg = BaselineStrategyConfig(surprise_threshold=0.05, hold_days=10, quantity=20)

    cfg_bt = _cfg(tmp_path, ExecutionMode.BACKTEST, "bt")
    res_bt = run_backtest(cfg_bt, strategy="earnings_baseline", baseline=baseline_cfg)

    def make(comp: SpineComponents) -> Strategy:
        return EarningsBaselineStrategy(baseline_cfg)

    cfg_paper = _cfg(tmp_path, ExecutionMode.PAPER, "paper")
    res_paper = run_paper(cfg_paper, make_strategy=make)

    store_bt = StateStore(cfg_bt.state.db_path)
    store_paper = StateStore(cfg_paper.state.db_path)

    sig_bt = _fills_signature(store_bt, "hello_world", "backtest")
    sig_paper = _fills_signature(store_paper, "hello_world", "paper")

    assert sig_bt  # something actually traded
    assert sig_bt == sig_paper
    assert res_bt.steps == res_paper.steps
    assert res_bt.fills == res_paper.fills

    # Equity curves match point-for-point (mode label differs, values do not).
    curve_bt = [round(p["total_equity"], 6) for p in store_bt.get_equity_curve("hello_world", "backtest")]
    curve_paper = [
        round(p["total_equity"], 6) for p in store_paper.get_equity_curve("hello_world", "paper")
    ]
    assert curve_bt == curve_paper


def test_strategy_factory_builds_both_strategies():
    hw = make_strategy_factory("hello_world")
    eb = make_strategy_factory("earnings_baseline")
    assert callable(hw)
    assert callable(eb)


def test_next_bar_execution_fills_on_first_quote_after_signal(tmp_path: Path):
    baseline_cfg = BaselineStrategyConfig(surprise_threshold=0.05, hold_days=20, quantity=10)
    cfg, signal_ts, next_quote_ts = _cfg_with_execution_timing(tmp_path, "next_bar", "next")
    run_backtest(cfg, strategy="earnings_baseline", baseline=baseline_cfg)

    fills = StateStore(cfg.state.db_path).get_fills("hello_world", "backtest")
    assert fills
    first_fill = fills[0]
    assert first_fill["filled_at"] > signal_ts
    assert first_fill["filled_at"] == next_quote_ts


def test_same_bar_execution_is_selectable(tmp_path: Path):
    baseline_cfg = BaselineStrategyConfig(surprise_threshold=0.05, hold_days=20, quantity=10)
    cfg, signal_ts, _ = _cfg_with_execution_timing(tmp_path, "same_bar", "same")
    run_backtest(cfg, strategy="earnings_baseline", baseline=baseline_cfg)

    fills = StateStore(cfg.state.db_path).get_fills("hello_world", "backtest")
    assert fills
    assert fills[0]["filled_at"] == signal_ts

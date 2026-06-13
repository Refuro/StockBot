"""Phase 1 tests — historical data feed (1a) and backtest runner (1b)."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.config import AppConfig, ExecutionMode, load_config
from stockbot.core.datafeed.base import PointInTimeViolation
from stockbot.core.datafeed.historical import HistoricalReplayFeed
from stockbot.core.state.store import StateStore
from stockbot.harness.backtest import BaselineStrategyConfig, run_backtest
from stockbot.harness.metrics import (
    EquityPoint,
    compute_backtest_metrics,
    infer_periods_per_year,
    period_returns,
    sharpe_ratio,
)


def _backtest_cfg(tmp_path: Path) -> AppConfig:
    base = load_config().model_dump()
    return AppConfig(
        **{
            **base,
            "mode": ExecutionMode.BACKTEST,
            "state": {"db_path": str(tmp_path / "phase1.db")},
            "datafeed": {"backtest": "historical_csv", "paper": "fake_live", "live": "fake_live"},
            "historical_data": {
                "prices_path": "fixtures/historical/prices.csv",
                "events_path": "fixtures/historical/events.csv",
            },
        }
    )


def test_historical_datafeed_replays_as_of_time():
    feed = HistoricalReplayFeed.from_csv(
        prices_path="fixtures/historical/prices.csv",
        events_path="fixtures/historical/events.csv",
    )

    q0 = feed.get_quote("FAKE")
    assert q0.as_of <= feed.now()
    assert feed.get_events() == []

    feed.advance()
    q1 = feed.get_quote("FAKE")
    events = feed.get_events()
    assert q1.as_of <= feed.now()
    assert len(events) == 1
    assert events[0].event_type == "earnings"
    assert events[0].as_of <= feed.now()


def test_historical_datafeed_rejects_future_peek():
    feed = HistoricalReplayFeed.from_csv(
        prices_path="fixtures/historical/prices.csv",
        events_path="fixtures/historical/events.csv",
    )
    future = feed.now() + timedelta(days=30)
    try:
        feed.get_quote_at("FAKE", future)
        assert False, "Expected PointInTimeViolation"
    except PointInTimeViolation:
        pass


def test_historical_datafeed_events_use_incremental_cursor():
    feed = HistoricalReplayFeed.from_csv(
        prices_path="fixtures/historical/prices.csv",
        events_path="fixtures/historical/events.csv",
    )
    # First call: no visible events yet.
    assert feed.get_events() == []
    # Advance until first event time.
    while True:
        if feed.get_events():
            break
        if not feed.advance():
            break
    first_batch = feed.get_events(since=feed.now() - timedelta(days=365))
    assert len(first_batch) >= 1
    # Cursor behavior: repeated call without advancing returns only new events.
    assert feed.get_events() == []


def test_backtest_runner_persists_snapshots_and_fills(tmp_path: Path):
    cfg = _backtest_cfg(tmp_path)
    result = run_backtest(cfg)

    store = StateStore(cfg.state.db_path)
    curve = store.get_equity_curve("hello_world", "backtest")
    run_row = store.get_run(result.run_id)

    assert result.steps >= 5
    assert result.snapshots == len(curve)
    assert len(curve) >= 5
    assert result.fills >= 1
    assert run_row is not None
    assert run_row["status"] == "completed"


def _write_multi_year_dataset(prices_path: Path, events_path: Path) -> None:
    prices_path.parent.mkdir(parents=True, exist_ok=True)
    start = date(2021, 1, 1)
    end = date(2024, 1, 1)  # 3 years
    day = start

    with prices_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "symbol", "price", "bid", "ask", "volume"])
        i = 0
        while day <= end:
            if day.weekday() < 5:
                # deterministic upward drift with low noise
                price = 50.0 + i * 0.03 + (i % 5) * 0.01
                ts = datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc).isoformat()
                writer.writerow([ts, "FAKE", f"{price:.4f}", f"{price-0.01:.4f}", f"{price+0.01:.4f}", 100000 + i])
                i += 1
            day += timedelta(days=1)

    with events_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        # quarterly earnings beats to trigger baseline entries
        event_day = date(2021, 1, 15)
        idx = 0
        while event_day <= end:
            # push to weekday
            while event_day.weekday() >= 5:
                event_day += timedelta(days=1)
            ts = datetime(event_day.year, event_day.month, event_day.day, 14, 30, tzinfo=timezone.utc).isoformat()
            payload = '{"surprise_pct": 0.06, "earnings_date": "' + event_day.isoformat() + '"}'
            writer.writerow([f"earn-{idx}", "earnings", "FAKE", ts, payload])
            idx += 1
            event_day += timedelta(days=90)


def _baseline_cfg(
    tmp_path: Path,
    slippage: float,
    suffix: str,
    simulated_overrides: dict | None = None,
) -> AppConfig:
    prices = tmp_path / f"prices_{suffix}.csv"
    events = tmp_path / f"events_{suffix}.csv"
    _write_multi_year_dataset(prices, events)
    base = load_config().model_dump()
    return AppConfig(
        **{
            **base,
            "mode": ExecutionMode.BACKTEST,
            "state": {"db_path": str(tmp_path / f"phase1_{suffix}.db")},
            "simulated_broker": {
                "initial_cash": 100_000.0,
                "slippage_pct": slippage,
                **(simulated_overrides or {}),
            },
            "datafeed": {"backtest": "historical_csv", "paper": "fake_live", "live": "fake_live"},
            "historical_data": {"prices_path": str(prices), "events_path": str(events)},
        }
    )


def test_baseline_backtest_reproducible_and_slippage_drag(tmp_path: Path):
    baseline = BaselineStrategyConfig(surprise_threshold=0.05, hold_days=10, quantity=20)

    cfg_no_slip_a = _baseline_cfg(tmp_path, slippage=0.0, suffix="a")
    res_a = run_backtest(cfg_no_slip_a, strategy="earnings_baseline", baseline=baseline)
    store_a = StateStore(cfg_no_slip_a.state.db_path)
    curve_a = store_a.get_equity_curve("hello_world", "backtest")
    final_a = curve_a[-1]["total_equity"]

    cfg_no_slip_b = _baseline_cfg(tmp_path, slippage=0.0, suffix="b")
    res_b = run_backtest(cfg_no_slip_b, strategy="earnings_baseline", baseline=baseline)
    store_b = StateStore(cfg_no_slip_b.state.db_path)
    curve_b = store_b.get_equity_curve("hello_world", "backtest")
    final_b = curve_b[-1]["total_equity"]

    # reproducible: identical run settings give same outputs
    assert res_a.steps == res_b.steps
    assert res_a.fills == res_b.fills
    assert final_a == final_b

    cfg_with_slip = _baseline_cfg(tmp_path, slippage=0.003, suffix="c")
    res_c = run_backtest(cfg_with_slip, strategy="earnings_baseline", baseline=baseline)
    store_c = StateStore(cfg_with_slip.state.db_path)
    curve_c = store_c.get_equity_curve("hello_world", "backtest")
    final_c = curve_c[-1]["total_equity"]

    # same trade path but slippage drags return
    assert res_c.steps == res_a.steps
    assert res_c.fills == res_a.fills
    assert final_c < final_a


def test_metrics_computed_from_persisted_data(tmp_path: Path):
    baseline = BaselineStrategyConfig(surprise_threshold=0.05, hold_days=10, quantity=20)
    cfg = _baseline_cfg(tmp_path, slippage=0.003, suffix="metrics")
    result = run_backtest(cfg, strategy="earnings_baseline", baseline=baseline)

    store = StateStore(cfg.state.db_path)
    metrics = compute_backtest_metrics(store, "hello_world", "backtest")

    assert result.metrics == metrics.to_dict()
    assert metrics.snapshot_count >= 500
    assert metrics.fill_count > 0
    assert metrics.closed_trades > 0
    assert metrics.total_return is not None
    assert metrics.max_drawdown >= 0.0
    assert metrics.hit_rate is not None
    assert metrics.turnover is not None
    assert metrics.total_slippage_cost > 0.0


def _curve_from_spacing(start: datetime, spacing: timedelta, equities: list[float]) -> list[EquityPoint]:
    out: list[EquityPoint] = []
    ts = start
    for eq in equities:
        out.append(EquityPoint(as_of=ts.isoformat(), cash=eq, total_equity=eq))
        ts += spacing
    return out


def test_periods_per_year_inferred_from_snapshot_cadence():
    start = datetime(2023, 1, 2, 14, 30, tzinfo=timezone.utc)
    equities = [100.0 + i for i in range(40)]

    daily = _curve_from_spacing(start, timedelta(days=1), equities)
    minute = _curve_from_spacing(start, timedelta(minutes=1), equities)
    hourly = _curve_from_spacing(start, timedelta(hours=1), equities)

    # Daily snapshots ≈ 365.25 calendar periods/yr (not the hardcoded 252).
    assert abs(infer_periods_per_year(daily) - 365.25) < 1e-6
    # Minute snapshots: ~525,960 periods/yr.
    assert abs(infer_periods_per_year(minute) - (365.25 * 24 * 60)) < 1e-3
    # Hourly snapshots: ~8,766 periods/yr.
    assert abs(infer_periods_per_year(hourly) - (365.25 * 24)) < 1e-6


def test_intraday_sharpe_annualizes_correctly():
    # Same return *series*, different cadence -> different annualized Sharpe.
    start = datetime(2023, 1, 2, 14, 30, tzinfo=timezone.utc)
    equities = [100.0, 101.0, 100.5, 101.5, 102.0, 101.8, 102.5, 103.0]

    daily = _curve_from_spacing(start, timedelta(days=1), equities)
    minute = _curve_from_spacing(start, timedelta(minutes=1), equities)

    rets = period_returns(daily)
    assert period_returns(minute) == rets  # identical return series

    ppy_daily = infer_periods_per_year(daily)
    ppy_minute = infer_periods_per_year(minute)

    sharpe_daily = sharpe_ratio(rets, periods_per_year=ppy_daily)
    sharpe_minute = sharpe_ratio(rets, periods_per_year=ppy_minute)

    # Annualization factor differs by sqrt(periods ratio); minute >> daily.
    import math

    expected_ratio = math.sqrt(ppy_minute / ppy_daily)
    assert sharpe_minute / sharpe_daily == pytest.approx(expected_ratio, rel=1e-9)


def test_metrics_reproducible_across_reruns(tmp_path: Path):
    baseline = BaselineStrategyConfig(surprise_threshold=0.05, hold_days=10, quantity=20)

    cfg_a = _baseline_cfg(tmp_path, slippage=0.003, suffix="m_a")
    run_backtest(cfg_a, strategy="earnings_baseline", baseline=baseline)
    metrics_a = compute_backtest_metrics(StateStore(cfg_a.state.db_path), "hello_world", "backtest")

    cfg_b = _baseline_cfg(tmp_path, slippage=0.003, suffix="m_b")
    run_backtest(cfg_b, strategy="earnings_baseline", baseline=baseline)
    metrics_b = compute_backtest_metrics(StateStore(cfg_b.state.db_path), "hello_world", "backtest")

    assert metrics_a.to_dict() == metrics_b.to_dict()


def test_cost_model_scales_slippage_for_liquidity(tmp_path: Path):
    baseline = BaselineStrategyConfig(surprise_threshold=0.05, hold_days=20, quantity=200)

    cfg_liquid = _baseline_cfg(
        tmp_path,
        slippage=0.0,
        suffix="liq",
        simulated_overrides={
            "initial_cash": 500_000.0,  # headroom so the 200-share entry clears the 5% position cap
            "cost_profile": "robinhood_retail",
            "spread_bps": 8.0,
            "impact_coeff_bps": 6.0,
            "adv_shares": 20_000_000.0,
            "execution_timing": "same_bar",
        },
    )
    run_backtest(cfg_liquid, strategy="earnings_baseline", baseline=baseline)
    fills_liquid = StateStore(cfg_liquid.state.db_path).get_fills("hello_world", "backtest")
    assert fills_liquid
    liquid_slip = fills_liquid[0]["slippage_pct"]

    cfg_thin = _baseline_cfg(
        tmp_path,
        slippage=0.0,
        suffix="thin",
        simulated_overrides={
            "initial_cash": 500_000.0,  # headroom so the 200-share entry clears the 5% position cap
            "cost_profile": "robinhood_retail",
            "spread_bps": 8.0,
            "impact_coeff_bps": 6.0,
            "adv_shares": 20_000_000.0,
            "symbol_costs": {"FAKE": {"adv_shares": 80_000.0, "spread_bps": 30.0}},
            "execution_timing": "same_bar",
        },
    )
    run_backtest(cfg_thin, strategy="earnings_baseline", baseline=baseline)
    fills_thin = StateStore(cfg_thin.state.db_path).get_fills("hello_world", "backtest")
    assert fills_thin
    thin_slip = fills_thin[0]["slippage_pct"]

    assert thin_slip > liquid_slip

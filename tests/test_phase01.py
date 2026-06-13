"""Phase 0.1 tests — persistence, reconciliation, PIT, audit log, run metadata."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.config import AppConfig, load_config
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.datafeed.base import PointInTimeViolation
from stockbot.core.datafeed.fake import FakeLiveFeed, FakeReplayFeed
from stockbot.core.state.store import StateStore
from stockbot.harness.metrics import load_equity_curve, max_drawdown, total_return
from stockbot.harness.run_context import hash_config
from stockbot.harness.runtime import build_spine, run_hello_world
from stockbot.types import (
    ExecutionMode,
    OrderRequest,
    PortfolioSnapshot,
    Position,
    Side,
)

BOT = "hello_world"


def _cfg(db_path: str, mode: ExecutionMode) -> AppConfig:
    base = load_config().model_dump()
    return AppConfig(
        **{
            **base,
            "mode": mode,
            "state": {"db_path": db_path},
            "simulated_broker": {
                **base["simulated_broker"],
                "execution_timing": "same_bar",
            },
        }
    )


# ----------------------- 0.1a: persistence -----------------------


def test_record_and_read_equity_curve(tmp_path: Path):
    store = StateStore(str(tmp_path / "curve.db"))
    base = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)
    for i, equity in enumerate((100_000.0, 101_000.0, 99_500.0)):
        snap = PortfolioSnapshot(
            cash=equity,
            positions=[],
            total_equity=equity,
            as_of=base + timedelta(days=i),
        )
        store.record_snapshot(snap, bot_id=BOT, mode="backtest")

    curve = store.get_equity_curve(BOT, "backtest")
    assert [p["total_equity"] for p in curve] == [100_000.0, 101_000.0, 99_500.0]
    assert curve[0]["as_of"] < curve[-1]["as_of"]


def test_snapshot_persists_positions(tmp_path: Path):
    store = StateStore(str(tmp_path / "pos.db"))
    snap = PortfolioSnapshot(
        cash=98_998.5,
        positions=[Position(symbol="FAKE", quantity=10, avg_cost=100.15, market_value=1001.5)],
        total_equity=100_000.0,
        as_of=datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc),
    )
    store.record_snapshot(snap, bot_id=BOT, mode="paper")

    latest = store.get_latest_snapshot(BOT, "paper")
    assert latest is not None
    assert latest.cash == 98_998.5
    assert len(latest.positions) == 1
    assert latest.positions[0].symbol == "FAKE"
    assert latest.positions[0].quantity == 10


def test_metrics_read_from_db_without_broker(tmp_path: Path):
    """Acceptance: the equity curve is queryable from the DB; metrics need no broker."""
    db = str(tmp_path / "metrics.db")
    run_hello_world(_cfg(db, ExecutionMode.PAPER))

    # Fresh process: a brand-new store handle, no broker anywhere in memory.
    store = StateStore(db)
    curve = load_equity_curve(store, BOT, "paper")
    assert len(curve) >= 1
    assert curve[-1].total_equity > 0
    # Helpers run purely off persisted data.
    assert max_drawdown(curve) >= 0.0
    _ = total_return(curve)


# ----------------------- 0.1b: reconciliation -----------------------


def test_sync_positions_no_source_is_noop():
    broker = SimulatedBroker(initial_cash=10_000, slippage_pct=0.003, execution_timing="same_bar")
    snap = broker.sync_positions()
    assert snap.cash == 10_000
    assert snap.positions == []


def test_restart_resumes_with_zero_desync(tmp_path: Path):
    """Acceptance: a run killed mid-way and restarted resumes positions/cash, zero desync."""
    db = str(tmp_path / "resume.db")
    as_of = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)

    # --- "Process 1": trade, then persist a snapshot, then die. ---
    store1 = StateStore(db)
    b1 = SimulatedBroker(initial_cash=10_000, slippage_pct=0.003, execution_timing="same_bar")
    b1.set_reconciliation_source(store1, BOT, "paper")
    b1.set_mark_price("FAKE", 100.0)
    b1.submit_order(OrderRequest(symbol="FAKE", side=Side.BUY, quantity=10, bot_id=BOT), as_of)
    snap1 = b1.get_portfolio(as_of)
    store1.record_snapshot(snap1, bot_id=BOT, mode="paper")

    # --- "Process 2": fresh broker + fresh store handle; reconcile from DB. ---
    store2 = StateStore(db)
    b2 = SimulatedBroker(initial_cash=10_000, slippage_pct=0.003, execution_timing="same_bar")
    b2.set_reconciliation_source(store2, BOT, "paper")
    snap2 = b2.sync_positions()

    assert snap2.cash == snap1.cash
    assert {p.symbol: p.quantity for p in snap2.positions} == {
        p.symbol: p.quantity for p in snap1.positions
    }
    assert {p.symbol: p.avg_cost for p in snap2.positions} == {
        p.symbol: p.avg_cost for p in snap1.positions
    }


def test_runtime_resume_reconciles_prior_holdings(tmp_path: Path):
    db = str(tmp_path / "rt_resume.db")
    cfg = _cfg(db, ExecutionMode.PAPER)

    first = run_hello_world(cfg)
    assert first.hello_world.order_status.value == "filled"

    # New spine (fresh broker) resumes from the DB snapshot.
    _, bot = build_spine(cfg)
    resumed = bot._broker.sync_positions()  # noqa: SLF001
    held = {p.symbol: p.quantity for p in resumed.positions}
    assert held.get("FAKE") == cfg.hello_world.quantity


# ----------------------- 0.1c: active PIT enforcement -----------------------


def test_pit_is_enforced_inside_get_quote_for_replay_feed():
    """A future-dated quote must raise from inside get_quote()."""
    leaky_feed = FakeReplayFeed(steps=2, quote_time_skew_hours=1)
    with pytest.raises(PointInTimeViolation):
        leaky_feed.get_quote("FAKE")


def test_pit_is_enforced_inside_get_quote_and_events_for_live_feed():
    feed = FakeLiveFeed()
    quote = feed.get_quote("FAKE")
    events = feed.get_events()
    assert quote.as_of == feed.now()
    assert events[0].as_of == feed.now()


# ----------------------- 0.1d: decision audit log -----------------------


def test_decision_is_fully_replayable_from_stored_inputs(tmp_path: Path):
    """Acceptance: a recorded decision can be replayed from stored inputs."""
    db = str(tmp_path / "audit.db")
    cfg = _cfg(db, ExecutionMode.PAPER)
    result = run_hello_world(cfg)
    assert result.hello_world.decision_id is not None

    store = StateStore(db)
    recorded = store.get_decision(result.hello_world.decision_id)  # type: ignore[arg-type]
    assert recorded is not None
    assert recorded["prompt_version"] == "hello_world_v1"
    assert recorded["model_id"] == "none"
    assert recorded["run_id"] == result.run_id

    inputs = recorded["inputs"]
    assert inputs["symbol"] == "FAKE"
    assert inputs["quote"]["price"] == pytest.approx(100.0)
    assert inputs["order_request"]["quantity"] == cfg.hello_world.quantity
    assert inputs["risk_decision"]["approved"] is True
    assert inputs["order_result"]["status"] == "filled"
    assert "events" in inputs


# ----------------------- 0.1e: run metadata + logging -----------------------


def test_run_writes_db_row_and_logfile(tmp_path: Path):
    """Acceptance: every run writes a runs row and a logfile."""
    db = str(tmp_path / "runs.db")
    cfg = _cfg(db, ExecutionMode.PAPER)
    result = run_hello_world(cfg)
    assert result.run_id is not None

    store = StateStore(db)
    run = store.get_run(result.run_id)  # type: ignore[arg-type]
    assert run is not None
    assert run["status"] == "completed"
    assert run["finished_at"] is not None
    assert run["config_hash"] == hash_config(cfg)
    assert run["model_versions"]["strong_tier"] == cfg.llm.strong_tier

    log_path = Path(run["log_path"])
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "run started" in content
    assert "run finished" in content
    assert result.run_id in content

"""Phase 0 tests — three-mode spine exit criterion."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stockbot.config import AppConfig, load_config
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.calibration.brier import brier_score
from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.datafeed.base import PointInTimeViolation
from stockbot.core.datafeed.fake import FakeReplayFeed
from stockbot.core.risk.engine import RiskEngine
from stockbot.core.state.store import StateStore
from stockbot.harness.runtime import build_spine, run_hello_world
from stockbot.types import ExecutionMode, OrderRequest, OrderStatus, Side


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


def test_point_in_time_guard():
    feed = FakeReplayFeed(steps=3)
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(PointInTimeViolation):
        feed.validate_point_in_time(future)


def test_simulated_broker_slippage():
    broker = SimulatedBroker(initial_cash=10_000, slippage_pct=0.003, execution_timing="same_bar")
    broker.set_mark_price("FAKE", 100.0)
    req = OrderRequest(symbol="FAKE", side=Side.BUY, quantity=10, bot_id="test")
    as_of = datetime.now(timezone.utc)
    result = broker.submit_order(req, as_of)
    assert result.status == OrderStatus.FILLED
    assert result.fill is not None
    expected = 100.0 * (1 + 0.003 / 2)
    assert result.fill.price == pytest.approx(expected)
    assert result.fill.slippage_pct == 0.003


def test_risk_engine_position_cap():
    from stockbot.config import RiskConfig

    risk = RiskEngine(RiskConfig(max_position_pct=0.05))
    from stockbot.types import PortfolioSnapshot

    portfolio = PortfolioSnapshot(cash=100_000, total_equity=100_000, as_of=datetime.now(timezone.utc))
    risk.update_equity(portfolio)
    req = OrderRequest(symbol="FAKE", side=Side.BUY, quantity=100, bot_id="test")
    decision = risk.check_order(req, portfolio, mark_price=100.0)
    assert not decision.approved
    assert "cap" in decision.reason.lower()


def test_brier_score():
    assert brier_score(1.0, True) == 0.0
    assert brier_score(0.0, True) == 1.0
    assert brier_score(0.5, False) == 0.25


def test_calibration_ledger(tmp_db: str):
    store = StateStore(tmp_db)
    ledger = CalibrationLedger(store)
    ledger.record_prediction(
        thesis_id="t1",
        bot_id="hello_world",
        symbol="FAKE",
        direction="buy",
        stated_probability=0.60,
        trade_type="hello_world",
        mode="backtest",
    )
    score = ledger.resolve(
        thesis_id="t1",
        bot_id="hello_world",
        trade_type="hello_world",
        stated_probability=0.60,
        outcome=True,
        bucket="medium_confidence",
    )
    assert score == pytest.approx(0.16)
    stats = store.get_calibration_stats("hello_world")
    assert stats["count"] == 1


@pytest.mark.parametrize("mode", ["backtest", "paper", "live"])
def test_hello_world_all_modes(mode: str, tmp_path: Path):
    """Exit criterion: hello-world runs in all 3 modes with config-only change."""
    db_path = str(tmp_path / f"{mode}.db")
    base = load_config()
    config = AppConfig(
        **{
            **base.model_dump(),
            "mode": ExecutionMode(mode),
            "state": {"db_path": db_path},
            "simulated_broker": {
                **base.simulated_broker.model_dump(),
                "execution_timing": "same_bar",
            },
        }
    )
    result = run_hello_world(config)
    assert result.mode == ExecutionMode(mode)
    assert result.hello_world.order_status == OrderStatus.FILLED
    assert result.hello_world.fill_price is not None


def test_same_bot_code_all_modes(tmp_path: Path):
    """build_spine + HelloWorldBot is identical; only config differs."""
    results = []
    for mode in ("backtest", "paper", "live"):
        db = str(tmp_path / f"spine_{mode}.db")
        cfg = AppConfig(
            **{
                **load_config().model_dump(),
                "mode": ExecutionMode(mode),
                "state": {"db_path": db},
            }
        )
        _, bot = build_spine(cfg)
        results.append(type(bot).__name__)
    assert len(set(results)) == 1

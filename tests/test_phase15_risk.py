"""Phase 1.5 risk-enforcement regression tests.

The 1.5a refactor moved trading logic into `Strategy` objects driven by the
shared `drive_strategy` loop. These tests pin the invariant that the deterministic
RiskEngine is enforced *centrally* in that loop — no strategy can bypass it.
"""

from __future__ import annotations

from pathlib import Path

from stockbot.config import AppConfig, load_config
from stockbot.core.strategy import Strategy, StrategyContext
from stockbot.harness.loop import drive_strategy
from stockbot.harness.runtime import SpineComponents
from stockbot.types import ExecutionMode, OrderRequest, OrderStatus, OrderType, Side


class _GreedyStrategy(Strategy):
    """Tries to buy far above the position cap every tick."""

    def __init__(self, quantity: float) -> None:
        self._qty = quantity
        self.results: list = []

    @property
    def bot_id(self) -> str:
        return "greedy"

    def on_tick(self, ctx: StrategyContext) -> None:
        quote = ctx.datafeed.get_quote("FAKE")
        ctx.set_mark("FAKE", quote.price)
        order = OrderRequest(
            symbol="FAKE",
            side=Side.BUY,
            quantity=self._qty,
            order_type=OrderType.MARKET,
            bot_id=self.bot_id,
        )
        self.results.append(ctx.submit_and_record(order))


class _ModestStrategy(_GreedyStrategy):
    @property
    def bot_id(self) -> str:
        return "modest"


def _cfg(tmp_path: Path, max_position_pct: float) -> AppConfig:
    base = load_config().model_dump()
    base["mode"] = ExecutionMode.BACKTEST
    base["state"] = {"db_path": str(tmp_path / "risk.db")}
    base["datafeed"] = {"backtest": "fake_replay", "paper": "fake_live", "live": "fake_live"}
    base["risk"]["max_position_pct"] = max_position_pct
    base["simulated_broker"] = {"initial_cash": 100_000.0, "slippage_pct": 0.0}
    return AppConfig(**base)


def test_over_cap_entry_rejected_on_strategy_loop(tmp_path: Path):
    """A BUY far above the position cap is rejected by the centrally-enforced risk engine."""
    cfg = _cfg(tmp_path, max_position_pct=0.05)  # 5% of 100k = $5k cap
    strat = _GreedyStrategy(quantity=100_000)  # 100k shares >> cap at any sane price
    result = drive_strategy(lambda comp: strat, cfg, max_steps=1)

    assert result.fills == 0
    assert strat.results, "strategy should have attempted an order"
    last = strat.results[-1]
    assert last.status == OrderStatus.REJECTED
    assert "cap" in (last.rejection_reason or "").lower()


def test_within_cap_entry_passes_risk(tmp_path: Path):
    """A small BUW within the cap is accepted (proves we didn't break normal flow)."""
    cfg = _cfg(tmp_path, max_position_pct=0.50)  # generous cap
    strat = _ModestStrategy(quantity=10)
    result = drive_strategy(lambda comp: strat, cfg, max_steps=2)

    last = strat.results[-1]
    # next_bar execution returns PENDING at submit; the point is it was NOT rejected by risk
    assert last.status in {OrderStatus.PENDING, OrderStatus.FILLED}
    assert "cap" not in (last.rejection_reason or "").lower()

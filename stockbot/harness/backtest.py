"""Historical replay runner (Phase 1b/1.5a).

This module no longer contains any trading logic — it only selects a strategy and
hands it to the shared `drive_strategy` loop. The same strategy objects run under
`paper.py` and (later) the live runner unchanged.
"""

from __future__ import annotations

from typing import Literal

from stockbot.bots.earnings_baseline import BaselineStrategyConfig, EarningsBaselineStrategy
from stockbot.bots.hello_world import HelloWorldBot
from stockbot.config import AppConfig, load_config
from stockbot.harness.loop import StrategyFactory, StrategyRunResult, drive_strategy
from stockbot.harness.runtime import SpineComponents
from stockbot.core.strategy import Strategy
from stockbot.types import ExecutionMode

# Backwards-compatible alias: existing callers import BacktestResult.
BacktestResult = StrategyRunResult

StrategyName = Literal["hello_world", "earnings_baseline"]


def make_strategy_factory(
    strategy: StrategyName,
    baseline: BaselineStrategyConfig | None = None,
) -> StrategyFactory:
    """Return a factory that builds the named strategy from spine components."""
    if strategy == "hello_world":

        def _hello(comp: SpineComponents) -> Strategy:
            return HelloWorldBot(
                comp.config, comp.datafeed, comp.broker, comp.risk, comp.store, comp.calibration
            )

        return _hello

    if strategy == "earnings_baseline":
        bcfg = baseline or BaselineStrategyConfig()

        def _baseline(comp: SpineComponents) -> Strategy:
            return EarningsBaselineStrategy(bcfg)

        return _baseline

    raise ValueError(f"Unknown backtest strategy: {strategy}")


def run_backtest(
    config: AppConfig | None = None,
    *,
    resume: bool = False,
    max_steps: int | None = None,
    strategy: StrategyName = "hello_world",
    baseline: BaselineStrategyConfig | None = None,
) -> BacktestResult:
    """Advance historical time, run the chosen strategy each tick, persist outputs."""
    cfg = config or load_config()
    if cfg.mode != ExecutionMode.BACKTEST:
        raise ValueError("run_backtest requires mode=backtest")
    factory = make_strategy_factory(strategy, baseline)
    return drive_strategy(factory, cfg, resume=resume, max_steps=max_steps)

"""Harness entry points."""

from stockbot.harness.backtest import (
    BacktestResult,
    BaselineStrategyConfig,
    make_strategy_factory,
    run_backtest,
)
from stockbot.harness.loop import StrategyRunResult, drive_strategy
from stockbot.harness.metrics import BacktestMetrics, compute_backtest_metrics
from stockbot.harness.paper import run_paper
from stockbot.harness.runtime import SpineComponents, build_components, build_spine, run_hello_world

__all__ = [
    "build_spine",
    "build_components",
    "SpineComponents",
    "run_hello_world",
    "run_backtest",
    "run_paper",
    "drive_strategy",
    "make_strategy_factory",
    "BacktestResult",
    "StrategyRunResult",
    "BaselineStrategyConfig",
    "BacktestMetrics",
    "compute_backtest_metrics",
]

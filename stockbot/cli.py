"""CLI for running stockbot in any mode."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stockbot.config import AppConfig, load_config
from stockbot.harness.backtest import BaselineStrategyConfig, run_backtest
from stockbot.harness.runtime import run_hello_world
from stockbot.types import ExecutionMode


def _print_metrics(metrics: dict[str, float | int | None]) -> None:
    if not metrics:
        return
    print("Metrics:")
    for key in (
        "total_return",
        "sharpe_ratio",
        "periods_per_year",
        "hit_rate",
        "max_drawdown",
        "total_slippage_cost",
        "turnover",
        "closed_trades",
        "fill_count",
    ):
        if key in metrics:
            value = metrics[key]
            if isinstance(value, float):
                print(f"  {key}: {value:.6f}")
            else:
                print(f"  {key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stockbot — dual-bot trading system")
    parser.add_argument(
        "command",
        choices=["hello", "backtest", "backtest-baseline"],
        help="Command to run",
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live"],
        default=None,
        help="Execution mode (overrides config.yaml)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Optional cap on replay steps for backtests",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    config = load_config(config_path)
    if args.mode:
        config = AppConfig(**{**config.model_dump(), "mode": ExecutionMode(args.mode)})

    if args.command == "hello":
        result = run_hello_world(config)
        hw = result.hello_world
        print(f"Mode:       {result.mode.value}")
        print(f"Steps:      {result.steps}")
        print(f"Status:     {hw.order_status.value}")
        if hw.fill_price is not None:
            print(f"Fill price: {hw.fill_price:.4f}")
        if hw.rejection_reason:
            print(f"Rejected:   {hw.rejection_reason}")
        if hw.order_status.value != "filled":
            return 1
        print("Hello-world bot completed successfully.")
        return 0

    if args.command == "backtest":
        result = run_backtest(config, max_steps=args.max_steps, strategy="hello_world")
        print(f"Mode:      {config.mode.value}")
        print(f"Run ID:    {result.run_id}")
        print(f"Steps:     {result.steps}")
        print(f"Fills:     {result.fills}")
        print(f"Snapshots: {result.snapshots}")
        print(f"Window:    {result.start_as_of} -> {result.end_as_of}")
        _print_metrics(result.metrics)
        return 0

    if args.command == "backtest-baseline":
        result = run_backtest(
            config,
            max_steps=args.max_steps,
            strategy="earnings_baseline",
            baseline=BaselineStrategyConfig(),
        )
        print(f"Mode:      {config.mode.value}")
        print(f"Run ID:    {result.run_id}")
        print(f"Steps:     {result.steps}")
        print(f"Fills:     {result.fills}")
        print(f"Snapshots: {result.snapshots}")
        print(f"Window:    {result.start_as_of} -> {result.end_as_of}")
        _print_metrics(result.metrics)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

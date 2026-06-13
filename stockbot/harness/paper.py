"""Live-paper runner (Phase 1.5a foundation; Phase 4a fan-out).

Drives the *same* `Strategy` objects as the backtest harness through the shared
loop. The only difference from backtest is the config-selected
`DataFeed`/`Broker` and the recorded mode — there is no separate trading logic.

Phase 4a: `run_paper_fanout` races N strategy instances (distinct `bot_id`s)
against ONE shared feed at identical timestamps — the champion/challenger
harness. Each instance has its own simulated broker and risk engine; the shared
state DB separates equity curves, fills, theses, and calibration by `bot_id`
(and `strategy_bucket` within each bot).
"""

from __future__ import annotations

from stockbot.config import AppConfig, load_config
from stockbot.harness.loop import (
    StrategyFactory,
    StrategyRunResult,
    drive_strategies,
    drive_strategy,
)


def run_paper(
    config: AppConfig | None = None,
    *,
    make_strategy: StrategyFactory,
    resume: bool = False,
    max_steps: int | None = None,
) -> StrategyRunResult:
    """Run one strategy under the paper feed using the shared harness loop."""
    cfg = config or load_config()
    return drive_strategy(make_strategy, cfg, resume=resume, max_steps=max_steps)


def run_paper_fanout(
    config: AppConfig | None = None,
    *,
    make_strategies: list[StrategyFactory],
    resume: bool = False,
    max_steps: int | None = None,
) -> dict[str, StrategyRunResult]:
    """Race N strategies on one shared feed; results keyed by bot_id (Phase 4a)."""
    cfg = config or load_config()
    return drive_strategies(make_strategies, cfg, resume=resume, max_steps=max_steps)

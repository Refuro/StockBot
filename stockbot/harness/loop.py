"""The single strategy-driving loop shared by backtest, paper, and live (Phase 1.5a).

This module contains the ONLY trading loop in the system. It advances the clock,
calls `strategy.on_tick(ctx)`, and persists a snapshot each step. It holds **no**
trading logic — that lives entirely in the `Strategy`. Backtest, paper, and the
future live runner all call `drive_strategy` so the exact same strategy object
behaves identically regardless of mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from stockbot.config import AppConfig, load_config
from stockbot.core.broker.factory import create_broker
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.risk.engine import RiskEngine
from stockbot.core.strategy import Strategy, StrategyContext
from stockbot.harness.metrics import compute_backtest_metrics
from stockbot.harness.run_context import RunContext
from stockbot.harness.runtime import SpineComponents, build_components

StrategyFactory = Callable[[SpineComponents], Strategy]


@dataclass
class StrategyRunResult:
    run_id: str
    steps: int
    fills: int
    snapshots: int
    start_as_of: str
    end_as_of: str
    metrics: dict[str, float | int | None] = field(default_factory=dict)


class _FanoutFeedView:
    """Per-lane view of a shared feed.

    The underlying feed's `get_events()` cursor is consume-once; with N lanes
    the loop drains it once per tick and pushes the same events to every lane,
    so all strategies see the identical event stream at identical timestamps.
    All other feed methods delegate to the shared feed.
    """

    def __init__(self, feed: object) -> None:
        self._feed = feed
        self._queue: list = []

    def push_events(self, events: list) -> None:
        self._queue.extend(events)

    def get_events(self, since=None) -> list:
        if since is not None:
            return self._feed.get_events(since=since)
        out = self._queue
        self._queue = []
        return out

    def __getattr__(self, name: str):
        return getattr(self._feed, name)


@dataclass
class _Lane:
    """One strategy's isolated execution lane (own broker/risk/ctx; shared feed/store)."""

    strategy: Strategy
    ctx: StrategyContext
    run_ctx: RunContext
    feed_view: _FanoutFeedView | None = None


def drive_strategy(
    make_strategy: StrategyFactory,
    config: AppConfig | None = None,
    *,
    resume: bool = False,
    max_steps: int | None = None,
    components: SpineComponents | None = None,
) -> StrategyRunResult:
    """Drive one strategy over the wired feed until exhausted (or max_steps).

    Identical for every execution mode; the only difference between backtest,
    paper, and live is which `DataFeed`/`Broker` the config selects.
    """
    results = drive_strategies(
        [make_strategy], config, resume=resume, max_steps=max_steps, components=components
    )
    return next(iter(results.values()))


def drive_strategies(
    factories: list[StrategyFactory],
    config: AppConfig | None = None,
    *,
    resume: bool = False,
    max_steps: int | None = None,
    components: SpineComponents | None = None,
    lane_brokers: list | None = None,
    shadow_bot_ids: frozenset[str] = frozenset(),
) -> dict[str, StrategyRunResult]:
    """Drive N strategies on ONE shared feed at identical timestamps (Phase 4a).

    Each strategy gets its own broker + risk engine + run context (capital and
    halts are per-bot), but every `on_tick` sees the same feed clock, so the
    champion/challenger comparison is apples-to-apples. The shared `StateStore`
    separates everything by `bot_id`, which therefore must be distinct.

    `components` lets a runner inject pre-built spine components (2d: the paper
    runner wires a `LiveDataFeed` in place of the config-selected feed), and
    `lane_brokers` injects one pre-built broker per lane (2d: the daily paper
    process restores unsettled next-bar orders before driving the tick).

    `shadow_bot_ids` (5b): lanes whose bot_id is listed run in SHADOW mode —
    their context logs every order as a `shadow_order` decision row and can
    never be wired to a live broker (StrategyContext enforces it).
    """
    if not factories:
        raise ValueError("drive_strategies requires at least one strategy factory")
    if lane_brokers is not None and len(lane_brokers) != len(factories):
        raise ValueError("lane_brokers must match factories in length")
    cfg = config or load_config()
    comp = components or build_components(cfg)

    lanes: list[_Lane] = []
    # 2-R2: if lane k fails to construct, lanes 0..k-1 have live RunContexts —
    # finish them as "failed" so no `runs` row is stranded in "running".
    try:
        for i, make_strategy in enumerate(factories):
            # First lane reuses the spine's broker/risk; extra lanes get their own.
            if lane_brokers is not None:
                broker = lane_brokers[i]
            else:
                broker = comp.broker if i == 0 else create_broker(cfg)
            risk = comp.risk if i == 0 else RiskEngine(cfg.risk)
            strategy = make_strategy(comp)
            if any(lane.strategy.bot_id == strategy.bot_id for lane in lanes):
                raise ValueError(f"Duplicate bot_id in fan-out: {strategy.bot_id!r}")

            if isinstance(broker, SimulatedBroker):
                broker.set_reconciliation_source(comp.store, strategy.bot_id, cfg.mode.value)

            run_ctx = RunContext.start(cfg, comp.store, strategy.bot_id)
            strategy.set_run_context(run_ctx)
            if resume:
                broker.sync_positions()

            # With multiple lanes the consume-once event cursor must be fanned out.
            feed_view = _FanoutFeedView(comp.datafeed) if len(factories) > 1 else None
            ctx = StrategyContext(
                config=cfg,
                datafeed=feed_view if feed_view is not None else comp.datafeed,
                broker=broker,
                risk=risk,
                store=comp.store,
                run_context=run_ctx,
                shadow=strategy.bot_id in shadow_bot_ids,
            )
            lanes.append(_Lane(strategy=strategy, ctx=ctx, run_ctx=run_ctx, feed_view=feed_view))
    except Exception:
        for lane in lanes:
            lane.run_ctx.finish(status="failed")
        raise

    steps = 0
    start_as_of = comp.datafeed.now().isoformat()
    # A crashed tick must not leave "completed" run rows behind: ops health
    # (and paper_tick_completed_today) treat the DB as authoritative, so a
    # mislabelled run reads as a healthy tick that never happened.
    tick_ok = False
    try:
        while True:
            if len(lanes) > 1:
                fresh_events = comp.datafeed.get_events()
                for lane in lanes:
                    assert lane.feed_view is not None
                    lane.feed_view.push_events(fresh_events)
            for lane in lanes:
                pending_results = lane.ctx.settle_pending_orders()
                if pending_results:
                    lane.run_ctx.info(
                        "pending orders settled",
                        extra_fields={
                            "bot_id": lane.strategy.bot_id,
                            "as_of": lane.ctx.now.isoformat(),
                            "count": len(pending_results),
                            "filled": sum(1 for r in pending_results if r.fill is not None),
                        },
                    )
                # Shot 12: settle live fills from the venue (no-op for sim brokers).
                # Catches the prior session's async fills (placed at the 5 PM tick,
                # filled at this session's open). Idempotent with the standalone CLI.
                lane.ctx.reconcile_live_fills()
                lane.strategy.on_tick(lane.ctx)
            steps += 1

            for lane in lanes:
                snapshot = lane.ctx.broker.get_portfolio(lane.ctx.now)
                comp.store.record_snapshot(snapshot, bot_id=lane.strategy.bot_id, mode=cfg.mode.value)
                # Update deterministic risk state (drawdown / daily-loss halts) so the
                # next tick's entries are gated on current equity. Enforcement of the
                # halt happens centrally in StrategyContext.submit_and_record.
                lane.ctx.risk.update_equity(snapshot)
                lane.run_ctx.info(
                    "strategy tick",
                    extra_fields={
                        "bot_id": lane.strategy.bot_id,
                        "step": steps,
                        "as_of": lane.ctx.now.isoformat(),
                        "equity": snapshot.total_equity,
                    },
                )

            if max_steps is not None and steps >= max_steps:
                break
            if not comp.datafeed.advance():
                break

        results: dict[str, StrategyRunResult] = {}
        for lane in lanes:
            bot_id = lane.strategy.bot_id
            metrics = compute_backtest_metrics(comp.store, bot_id, cfg.mode.value).to_dict()
            curve = comp.store.get_equity_curve(bot_id, cfg.mode.value)
            end_as_of = curve[-1]["as_of"].isoformat() if curve else start_as_of
            results[bot_id] = StrategyRunResult(
                run_id=lane.run_ctx.run_id,
                steps=steps,
                fills=len(comp.store.get_fills(bot_id, cfg.mode.value)),
                snapshots=len(curve),
                start_as_of=start_as_of,
                end_as_of=end_as_of,
                metrics=metrics,
            )
        tick_ok = True
        return results
    finally:
        for lane in lanes:
            lane.run_ctx.finish(status="completed" if tick_ok else "failed")

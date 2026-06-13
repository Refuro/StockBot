"""Runtime harness — wires spine components for any execution mode."""

from __future__ import annotations

from dataclasses import dataclass

from stockbot.bots.hello_world import HelloWorldBot, HelloWorldResult
from stockbot.config import AppConfig, load_config
from stockbot.core.broker.factory import create_broker
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.datafeed.factory import create_datafeed
from stockbot.core.risk.engine import RiskEngine
from stockbot.core.state.store import StateStore
from stockbot.harness.run_context import RunContext
from stockbot.types import ExecutionMode, OrderStatus


@dataclass
class RunResult:
    mode: ExecutionMode
    hello_world: HelloWorldResult
    steps: int
    run_id: str | None = None


@dataclass
class SpineComponents:
    """The wired spine: same objects in backtest, paper, and live (config picks impls)."""

    config: AppConfig
    datafeed: object
    broker: object
    risk: RiskEngine
    store: StateStore
    calibration: CalibrationLedger


def build_components(config: AppConfig | None = None) -> SpineComponents:
    """Construct the spine components from config, with no strategy attached."""
    cfg = config or load_config()
    datafeed = create_datafeed(cfg)
    broker = create_broker(cfg)
    store = StateStore(cfg.state.db_path)
    risk = RiskEngine(cfg.risk)
    calibration = CalibrationLedger(store)
    return SpineComponents(
        config=cfg,
        datafeed=datafeed,
        broker=broker,
        risk=risk,
        store=store,
        calibration=calibration,
    )


def build_spine(config: AppConfig | None = None) -> tuple[AppConfig, HelloWorldBot]:
    """Construct the full stack from config. Same code for all modes."""
    comp = build_components(config)
    bot = HelloWorldBot(comp.config, comp.datafeed, comp.broker, comp.risk, comp.store, comp.calibration)

    # 0.1b: point the simulated broker at the DB so it can reconcile on startup.
    if isinstance(comp.broker, SimulatedBroker):
        comp.broker.set_reconciliation_source(comp.store, HelloWorldBot.BOT_ID, comp.config.mode.value)

    return comp.config, bot


def _persist_snapshot(bot: HelloWorldBot, cfg: AppConfig) -> None:
    """0.1a: persist the broker's portfolio snapshot for the equity curve."""
    broker = bot._broker  # noqa: SLF001 — harness owns wiring
    store = bot._store  # noqa: SLF001
    datafeed = bot._datafeed  # noqa: SLF001
    snapshot = broker.get_portfolio(datafeed.now())
    store.record_snapshot(snapshot, bot_id=HelloWorldBot.BOT_ID, mode=cfg.mode.value)


def run_hello_world(config: AppConfig | None = None, resume: bool = False) -> RunResult:
    """Run hello-world bot end-to-end. Backtest advances replay; paper/live run once.

    When ``resume`` is set, the broker reconciles from the last persisted snapshot
    before trading so a killed-and-restarted run continues with zero desync (0.1b).
    """
    cfg, bot = build_spine(config)
    datafeed = bot._datafeed  # noqa: SLF001 — harness owns wiring
    store = bot._store  # noqa: SLF001

    run_ctx = RunContext.start(cfg, store, HelloWorldBot.BOT_ID)
    bot.set_run_context(run_ctx)

    if resume:
        bot._broker.sync_positions()  # noqa: SLF001 — restore from source of truth

    steps = 0
    result: HelloWorldResult

    try:
        if cfg.mode == ExecutionMode.BACKTEST:
            while True:
                result = bot.run_once()
                steps += 1
                _persist_snapshot(bot, cfg)
                if result.order_status == OrderStatus.FILLED:
                    break
                if not datafeed.advance():
                    break
        else:
            result = bot.run_once()
            _persist_snapshot(bot, cfg)
            steps = 1

        run_ctx.info(
            "hello-world tick complete",
            extra_fields={
                "steps": steps,
                "order_status": result.order_status.value,
                "decision_id": result.decision_id,
            },
        )
        return RunResult(mode=cfg.mode, hello_world=result, steps=steps, run_id=run_ctx.run_id)
    finally:
        run_ctx.finish()

"""Earnings-surprise baseline as a first-class Strategy (Phase 1.5a).

This is the "dumb baseline" the plan calls for: buy any earnings surprise above
a threshold, hold N days, then sell. It implements `Strategy` and is driven by
the same harness loop in backtest and paper — there is no trading logic in the
harness itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from stockbot.core.strategy import Strategy, StrategyContext
from stockbot.types import OrderRequest, OrderStatus, OrderType, Side

DEFAULT_BOT_ID = "hello_world"


@dataclass(frozen=True)
class BaselineStrategyConfig:
    """Dumb baseline: buy surprise>threshold, hold N days, sell."""

    surprise_threshold: float = 0.05
    hold_days: int = 10
    quantity: float = 10.0


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class EarningsBaselineStrategy(Strategy):
    """Mechanical PEAD baseline. No LLM — pure rules over earnings events."""

    def __init__(self, config: BaselineStrategyConfig | None = None, bot_id: str = DEFAULT_BOT_ID) -> None:
        self._cfg = config or BaselineStrategyConfig()
        self._bot_id = bot_id
        self._seen_event_ids: set[str] = set()
        self._open_positions: dict[str, datetime] = {}
        self._run_context: Any = None

    @property
    def bot_id(self) -> str:
        return self._bot_id

    def set_run_context(self, run_context: Any) -> None:
        self._run_context = run_context

    def on_tick(self, ctx: StrategyContext) -> None:
        now = ctx.now
        events = ctx.datafeed.get_events()

        # Entry: fresh earnings surprises above threshold.
        for event in events:
            if event.event_id in self._seen_event_ids:
                continue
            self._seen_event_ids.add(event.event_id)
            if event.event_type != "earnings":
                continue
            surprise = _safe_float(event.payload.get("surprise_pct"), 0.0)
            if surprise <= self._cfg.surprise_threshold:
                continue
            if event.symbol in self._open_positions:
                continue
            quote = ctx.datafeed.get_quote(event.symbol)
            ctx.set_mark(event.symbol, quote.price)
            order = OrderRequest(
                symbol=event.symbol,
                side=Side.BUY,
                quantity=self._cfg.quantity,
                order_type=OrderType.MARKET,
                bot_id=self._bot_id,
            )
            result = ctx.submit_and_record(order)
            if result.fill is not None or result.status == OrderStatus.PENDING:
                self._open_positions[event.symbol] = now

        # Exit: time-stop after hold_days.
        for symbol, entry_ts in list(self._open_positions.items()):
            if now < (entry_ts + timedelta(days=self._cfg.hold_days)):
                continue
            quote = ctx.datafeed.get_quote(symbol)
            ctx.set_mark(symbol, quote.price)
            snapshot = ctx.broker.get_portfolio(now)
            pos = next((p for p in snapshot.positions if p.symbol == symbol), None)
            if pos is None or pos.quantity <= 0:
                self._open_positions.pop(symbol, None)
                continue
            sell = OrderRequest(
                symbol=symbol,
                side=Side.SELL,
                quantity=pos.quantity,
                order_type=OrderType.MARKET,
                bot_id=self._bot_id,
            )
            ctx.submit_and_record(sell)
            self._open_positions.pop(symbol, None)

"""Strategy protocol + execution context (Phase 1.5a).

The `Strategy` interface is the core of "one decision codebase, three modes."
A strategy implements `on_tick(ctx)` and never knows whether it runs under
historical backtest, live paper, or live trading — the harness loop is identical
in all three; only the wired `DataFeed`/`Broker` (chosen by config) differ.

`StrategyContext` exposes the as-of `DataFeed`, the `Broker`, the `StateStore`,
and the deterministic `RiskEngine`. Order *execution plumbing* (marking, fill
persistence) lives here so strategies stay pure decision logic.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

from stockbot.core.broker.base import Broker
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.datafeed.base import DataFeed
from stockbot.core.risk.engine import RiskEngine
from stockbot.core.state.store import StateStore
from stockbot.types import OrderRequest, OrderResult, OrderStatus, Side

if TYPE_CHECKING:
    from stockbot.config import AppConfig


class StrategyContext:
    """As-of view of the world handed to a strategy each tick."""

    def __init__(
        self,
        config: AppConfig,
        datafeed: DataFeed,
        broker: Broker,
        risk: RiskEngine,
        store: StateStore,
        run_context: Any = None,
        shadow: bool = False,
    ) -> None:
        # 5b: a shadow lane runs live data + live decisions but must be
        # PHYSICALLY unable to reach a live broker — enforced at construction,
        # not by convention. Live brokers carry is_live=True (LiveBrokerRails).
        if shadow and getattr(broker, "is_live", False):
            raise ValueError(
                "shadow context cannot be wired to a live broker "
                f"({type(broker).__name__}); shadow lanes use simulated bookkeeping only"
            )
        self.config = config
        self.datafeed = datafeed
        self.broker = broker
        self.risk = risk
        self.store = store
        self.run_context = run_context
        self.shadow = shadow

    @property
    def mode(self) -> str:
        return self.config.mode.value

    @property
    def now(self) -> datetime:
        return self.datafeed.now()

    def set_mark(self, symbol: str, price: float) -> None:
        """Inform a simulated broker of the current mark price (no-op for live)."""
        if isinstance(self.broker, SimulatedBroker):
            self.broker.set_mark_price(symbol, price)

    def _mark_for(self, symbol: str) -> float | None:
        """Best available point-in-time mark for `symbol` (None if unavailable)."""
        try:
            return self.datafeed.get_quote(symbol).price
        except Exception:
            return None

    def _rejected(self, order: OrderRequest, reason: str) -> OrderResult:
        return OrderResult(
            order_id=str(uuid.uuid4()),
            status=OrderStatus.REJECTED,
            request=order,
            rejection_reason=reason,
        )

    def submit_and_record(self, order: OrderRequest) -> OrderResult:
        """Risk-gate, submit, and persist any resulting fill.

        Risk enforcement is centralized here so NO strategy can bypass it.
        Entries (BUY) are gated by the deterministic `RiskEngine` (position caps,
        daily-loss / drawdown halts, order-rate limit). Exits (SELL) always pass
        through so positions can still be closed during a halt.
        """
        if order.side == Side.BUY:
            mark = self._mark_for(order.symbol)
            if mark is None:
                return self._rejected(order, f"No mark price available for {order.symbol}")
            portfolio = self.broker.get_portfolio(self.now)
            decision = self.risk.check_order(order, portfolio, mark)
            if not decision.approved:
                if self.run_context is not None:
                    self.run_context.info(
                        "order rejected by risk",
                        extra_fields={
                            "bot_id": order.bot_id,
                            "symbol": order.symbol,
                            "side": order.side.value,
                            "quantity": order.quantity,
                            "reason": decision.reason,
                            "as_of": self.now.isoformat(),
                        },
                    )
                return self._rejected(order, decision.reason)
            self.risk.record_order(self.now.date())

        if self.shadow:
            # 5b: log exactly what WOULD be sent to a live broker. The order
            # still flows to the lane's simulated broker so positions/exits/
            # calibration behave identically to the paper lanes — but a live
            # broker is unreachable from a shadow context (ctor guard above).
            mark = self._mark_for(order.symbol)
            self.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=order.bot_id,
                decision_type="shadow_order",
                action=order.side.value,
                inputs={
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": order.quantity,
                    "order_type": order.order_type.value,
                    "limit_price": order.limit_price,
                    "mark_price": mark,
                    "notional_est": (abs(order.quantity) * mark) if mark else None,
                    "thesis_id": order.thesis_id,
                },
                mode=self.mode,
                as_of=self.now,
                run_id=getattr(self.run_context, "run_id", None),
                thesis_id=order.thesis_id,
            )

        result = self.broker.submit_order(order, self.now)
        if self.run_context is not None:
            self.run_context.info(
                "order submitted",
                extra_fields={
                    "bot_id": order.bot_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": order.quantity,
                    "status": result.status.value,
                    "as_of": self.now.isoformat(),
                },
            )
        if result.fill is not None:
            fill = result.fill
            self.store.record_fill(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                symbol=fill.symbol,
                side=fill.side.value,
                quantity=fill.quantity,
                price=fill.price,
                slippage_pct=fill.slippage_pct,
                bot_id=fill.bot_id,
                mode=self.mode,
                filled_at=fill.filled_at,
                thesis_id=order.thesis_id,
            )
        return result

    def reconcile_live_fills(self) -> Any:
        """Shot 12: pull realized LIVE fills into the DB (read-only at venue).

        The simulated settle path below handles paper/backtest. Live orders fill
        asynchronously at the next-open, AFTER the tick that placed them, so the
        realized price/time must be reconciled from the venue. Only acts for a
        live broker; for the simulated broker (paper/backtest) this is a no-op,
        so the sim settle path is untouched. Idempotent — safe every tick.
        """
        if not getattr(self.broker, "is_live", False):
            return None
        # Imported lazily so the simulated/backtest path never pulls the live
        # broker reconciliation module.
        from stockbot.core.broker.reconcile import reconcile_live_fills

        summary = reconcile_live_fills(self.store, self.broker, mode=self.mode)
        if self.run_context is not None and (summary.recorded or summary.updated or summary.unmatched):
            self.run_context.info(
                "live fills reconciled",
                extra_fields={"as_of": self.now.isoformat(), **summary.as_dict()},
            )
        return summary

    def settle_pending_orders(self) -> list[OrderResult]:
        """Fill simulated next-bar orders at the first quote strictly after signal time."""
        if not isinstance(self.broker, SimulatedBroker):
            return []

        for symbol in self.broker.pending_symbols():
            try:
                quote = self.datafeed.get_quote(symbol)
            except ValueError:
                continue
            self.broker.set_mark_price(symbol, quote.price)

        results = self.broker.process_pending_orders(self.now)
        for result in results:
            if result.fill is None:
                continue
            fill = result.fill
            self.store.record_fill(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                symbol=fill.symbol,
                side=fill.side.value,
                quantity=fill.quantity,
                price=fill.price,
                slippage_pct=fill.slippage_pct,
                bot_id=fill.bot_id,
                mode=self.mode,
                filled_at=fill.filled_at,
                thesis_id=result.request.thesis_id,
            )
        return results


class Strategy(ABC):
    """A trading strategy driven identically across backtest, paper, and live."""

    @property
    @abstractmethod
    def bot_id(self) -> str:
        """Stable identifier used to tag this strategy's rows in the state DB."""

    @abstractmethod
    def on_tick(self, ctx: StrategyContext) -> None:
        """React to the world as-of `ctx.now`. Called once per harness step."""

    def set_run_context(self, run_context: Any) -> None:  # noqa: B027 - optional hook
        """Optional: receive the active RunContext for audit logging."""

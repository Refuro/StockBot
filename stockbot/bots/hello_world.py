"""Hello-world bot — Phase 0 smoke test through the full stack."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stockbot.config import AppConfig, HelloWorldConfig
from stockbot.core.broker.base import Broker
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.datafeed.base import DataFeed
from stockbot.core.risk.engine import RiskEngine
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy, StrategyContext
from stockbot.harness.run_context import RunContext
from stockbot.types import ExecutionMode, OrderRequest, OrderStatus, OrderType, Side


@dataclass
class HelloWorldResult:
    mode: ExecutionMode
    order_status: OrderStatus
    fill_price: float | None
    rejection_reason: str | None
    thesis_id: str
    decision_id: str | None = None


class HelloWorldBot(Strategy):
    """Places one fake order through DataFeed → Risk → Broker → State."""

    BOT_ID = "hello_world"

    @property
    def bot_id(self) -> str:
        return self.BOT_ID

    def on_tick(self, ctx: StrategyContext) -> None:
        """Strategy entry point. Uses the components wired at construction, which
        the harness builds from the same spine that backs `ctx`."""
        self.run_once()

    def __init__(
        self,
        config: AppConfig,
        datafeed: DataFeed,
        broker: Broker,
        risk: RiskEngine,
        store: StateStore,
        calibration: CalibrationLedger,
        hw_config: HelloWorldConfig | None = None,
    ) -> None:
        self._config = config
        self._datafeed = datafeed
        self._broker = broker
        self._risk = risk
        self._store = store
        self._calibration = calibration
        self._hw = hw_config or config.hello_world
        self._run_context: RunContext | None = None

    def set_run_context(self, run_context: RunContext | None) -> None:
        self._run_context = run_context

    def _record_decision(
        self,
        decision_id: str,
        thesis_id: str | None,
        as_of: datetime,
        action: str,
        inputs: dict[str, Any],
    ) -> None:
        self._store.record_decision(
            decision_id=decision_id,
            run_id=self._run_context.run_id if self._run_context else None,
            thesis_id=thesis_id,
            bot_id=self.BOT_ID,
            decision_type="hello_world_trade",
            action=action,
            inputs=inputs,
            mode=self._config.mode.value,
            as_of=as_of,
            model_id="none",
            prompt_version="hello_world_v1",
        )

    def run_once(self) -> HelloWorldResult:
        as_of = self._datafeed.now()
        symbol = self._hw.symbol
        quote = self._datafeed.get_quote(symbol)

        if isinstance(self._broker, SimulatedBroker):
            self._broker.set_mark_price(symbol, quote.price)

        portfolio = self._broker.get_portfolio(as_of)
        self._risk.update_equity(portfolio)

        side = Side(self._hw.side)
        request = OrderRequest(
            symbol=symbol,
            side=side,
            quantity=self._hw.quantity,
            order_type=OrderType.MARKET,
            bot_id=self.BOT_ID,
        )

        decision = self._risk.check_order(request, portfolio, quote.price)
        thesis_id = str(uuid.uuid4())
        decision_id = str(uuid.uuid4())
        events = self._datafeed.get_events()
        inputs = {
            "as_of": as_of.isoformat(),
            "symbol": symbol,
            "quote": quote.model_dump(mode="json"),
            "portfolio": {
                "cash": portfolio.cash,
                "total_equity": portfolio.total_equity,
                "positions": [p.model_dump() for p in portfolio.positions],
            },
            "order_request": request.model_dump(),
            "risk_decision": {"approved": decision.approved, "reason": decision.reason},
            "events": [e.model_dump(mode="json") for e in events],
        }

        if not decision.approved:
            self._record_decision(decision_id, None, as_of, "reject", inputs)
            return HelloWorldResult(
                mode=self._config.mode,
                order_status=OrderStatus.REJECTED,
                fill_price=None,
                rejection_reason=decision.reason,
                thesis_id=thesis_id,
                decision_id=decision_id,
            )

        self._calibration.record_prediction(
            thesis_id=thesis_id,
            bot_id=self.BOT_ID,
            symbol=symbol,
            direction=side.value,
            stated_probability=0.60,
            trade_type="hello_world",
            mode=self._config.mode.value,
            rationale="Phase 0 smoke test",
        )

        result = self._broker.submit_order(request, as_of)
        inputs["order_result"] = {
            "status": result.status.value,
            "rejection_reason": result.rejection_reason,
            "fill": result.fill.model_dump(mode="json") if result.fill else None,
        }
        action = "fill" if result.status == OrderStatus.FILLED else "reject"
        self._record_decision(decision_id, thesis_id, as_of, action, inputs)

        if result.status == OrderStatus.FILLED and result.fill:
            self._risk.record_order(as_of.date())
            self._store.record_fill(
                fill_id=result.fill.fill_id,
                order_id=result.fill.order_id,
                symbol=result.fill.symbol,
                side=result.fill.side.value,
                quantity=result.fill.quantity,
                price=result.fill.price,
                slippage_pct=result.fill.slippage_pct,
                bot_id=result.fill.bot_id,
                mode=self._config.mode.value,
                filled_at=result.fill.filled_at,
            )
            # Resolve thesis immediately for smoke test (always "success" in Phase 0)
            bucket = self._calibration.bucket_for_probability(0.60)
            self._calibration.resolve(
                thesis_id=thesis_id,
                bot_id=self.BOT_ID,
                trade_type="hello_world",
                stated_probability=0.60,
                outcome=True,
                bucket=bucket,
            )

        return HelloWorldResult(
            mode=self._config.mode,
            order_status=result.status,
            fill_price=result.fill.price if result.fill else None,
            rejection_reason=result.rejection_reason,
            thesis_id=thesis_id,
            decision_id=decision_id,
        )

"""Deterministic risk engine: caps, circuit breakers. NO LLM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stockbot.config import RiskConfig
from stockbot.types import OrderRequest, PortfolioSnapshot, Side


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str = ""


class RiskEngine:
    """Enforces position limits, daily loss halt, drawdown halt, and order rate limits."""

    def __init__(self, config: RiskConfig) -> None:
        self._config = config
        self._orders_today: dict[date, int] = {}
        self._peak_equity: float | None = None
        self._day_start_equity: float | None = None
        self._current_day: date | None = None
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted

    def update_equity(self, portfolio: PortfolioSnapshot) -> None:
        """Call at each tick to track drawdown and daily P&L."""
        day = portfolio.as_of.date()
        equity = portfolio.total_equity

        if self._current_day != day:
            self._current_day = day
            self._day_start_equity = equity
            self._orders_today[day] = 0

        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity

        if self._peak_equity and self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            if drawdown >= self._config.max_drawdown_pct:
                self._halted = True

        if self._day_start_equity and self._day_start_equity > 0:
            daily_loss = (self._day_start_equity - equity) / self._day_start_equity
            if daily_loss >= self._config.max_daily_loss_pct:
                self._halted = True

    def check_order(
        self,
        request: OrderRequest,
        portfolio: PortfolioSnapshot,
        mark_price: float,
    ) -> RiskDecision:
        if self._halted:
            return RiskDecision(False, "Risk halt active (drawdown or daily loss limit)")

        day = portfolio.as_of.date()
        order_count = self._orders_today.get(day, 0)
        if order_count >= self._config.max_orders_per_day:
            return RiskDecision(False, f"Max orders per day ({self._config.max_orders_per_day}) reached")

        if request.side == Side.BUY:
            order_value = mark_price * request.quantity
            max_value = portfolio.total_equity * self._config.max_position_pct
            existing = next((p for p in portfolio.positions if p.symbol == request.symbol), None)
            existing_value = existing.market_value if existing else 0.0
            if existing_value + order_value > max_value:
                return RiskDecision(
                    False,
                    f"Position size would exceed {self._config.max_position_pct:.0%} cap",
                )
            if order_value > portfolio.cash:
                return RiskDecision(False, "Insufficient cash for order")

        return RiskDecision(True)

    def record_order(self, as_of_date: date) -> None:
        self._orders_today[as_of_date] = self._orders_today.get(as_of_date, 0) + 1

    def reset(self) -> None:
        self._orders_today.clear()
        self._peak_equity = None
        self._day_start_equity = None
        self._current_day = None
        self._halted = False

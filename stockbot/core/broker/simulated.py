"""Simulated broker with configurable slippage model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import TYPE_CHECKING

from stockbot.core.broker.base import Broker
from stockbot.types import (
    Fill,
    OrderRequest,
    OrderResult,
    OrderStatus,
    PortfolioSnapshot,
    Position,
    Side,
)

if TYPE_CHECKING:
    from stockbot.core.state.store import StateStore


@dataclass(frozen=True)
class _PendingOrder:
    order_id: str
    request: OrderRequest
    submitted_at: datetime


class SimulatedBroker(Broker):
    """Simulates fills with slippage. Same code path for backtest, paper, and live."""

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        slippage_pct: float = 0.003,
        execution_timing: str = "next_bar",
        cost_profile: str = "flat",
        spread_bps: float = 20.0,
        impact_coeff_bps: float = 15.0,
        adv_shares: float = 1_000_000.0,
        symbol_costs: dict[str, dict[str, float]] | None = None,
    ) -> None:
        if execution_timing not in {"same_bar", "next_bar"}:
            raise ValueError("execution_timing must be 'same_bar' or 'next_bar'")
        if cost_profile not in {"flat", "small_cap", "robinhood_retail"}:
            raise ValueError("cost_profile must be 'flat', 'small_cap', or 'robinhood_retail'")
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._slippage_pct = slippage_pct
        self._execution_timing = execution_timing
        self._cost_profile = cost_profile
        self._spread_bps = spread_bps
        self._impact_coeff_bps = impact_coeff_bps
        self._adv_shares = adv_shares
        self._symbol_costs = symbol_costs or {}
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []
        self._pending_orders: list[_PendingOrder] = []
        self._mark_prices: dict[str, float] = {}
        self._recon_store: StateStore | None = None
        self._recon_bot_id: str | None = None
        self._recon_mode: str | None = None

    @property
    def slippage_pct(self) -> float:
        return self._slippage_pct

    @property
    def execution_timing(self) -> str:
        return self._execution_timing

    @property
    def cost_profile(self) -> str:
        return self._cost_profile

    def set_reconciliation_source(self, store: StateStore, bot_id: str, mode: str) -> None:
        """Point this broker at the DB it should reconcile against on sync_positions()."""
        self._recon_store = store
        self._recon_bot_id = bot_id
        self._recon_mode = mode

    # 2d: a daily paper process runs ONE tick and exits, so unsettled next-bar
    # orders must survive process restarts. Positions/cash already do (via
    # sync_positions); these two methods round-trip the pending-order queue.
    def export_pending_orders(self) -> list[dict]:
        return [
            {
                "order_id": p.order_id,
                "request": p.request.model_dump(mode="json"),
                "submitted_at": p.submitted_at.isoformat(),
            }
            for p in self._pending_orders
        ]

    def restore_pending_orders(self, raw: list[dict]) -> None:
        self._pending_orders = [
            _PendingOrder(
                order_id=item["order_id"],
                request=OrderRequest.model_validate(item["request"]),
                submitted_at=datetime.fromisoformat(item["submitted_at"]),
            )
            for item in raw
        ]

    def set_mark_price(self, symbol: str, price: float) -> None:
        self._mark_prices[symbol] = price

    def pending_symbols(self) -> set[str]:
        return {pending.request.symbol for pending in self._pending_orders}

    def process_pending_orders(self, as_of: datetime) -> list[OrderResult]:
        """Execute all next-bar orders whose signal time is strictly before `as_of`."""
        if not self._pending_orders:
            return []

        keep: list[_PendingOrder] = []
        results: list[OrderResult] = []
        for pending in self._pending_orders:
            if pending.submitted_at >= as_of:
                keep.append(pending)
                continue
            results.append(self._execute_order(pending.order_id, pending.request, as_of))
        self._pending_orders = keep
        return results

    def _cost_params_for_symbol(self, symbol: str) -> tuple[float, float, float]:
        """Resolve spread/impact/ADV for one symbol under the selected profile."""
        profile_defaults: dict[str, tuple[float, float, float]] = {
            "flat": (0.0, 0.0, max(self._adv_shares, 1.0)),
            "small_cap": (
                35.0,
                28.0,
                350_000.0,
            ),
            # Approximation for U.S. retail market-order flow routed by Robinhood.
            "robinhood_retail": (
                8.0,
                6.0,
                5_000_000.0,
            ),
        }
        base_spread_bps, base_impact_bps, base_adv = profile_defaults[self._cost_profile]
        spread_bps = self._spread_bps if self._cost_profile != "flat" else 0.0
        impact_bps = self._impact_coeff_bps if self._cost_profile != "flat" else 0.0
        adv = max(self._adv_shares, 1.0)

        if self._spread_bps == 20.0 and self._cost_profile != "flat":
            spread_bps = base_spread_bps
        if self._impact_coeff_bps == 15.0 and self._cost_profile != "flat":
            impact_bps = base_impact_bps
        if self._adv_shares == 1_000_000.0 and self._cost_profile != "flat":
            adv = base_adv

        overrides = self._symbol_costs.get(symbol, {})
        spread_bps = float(overrides.get("spread_bps", spread_bps))
        impact_bps = float(overrides.get("impact_coeff_bps", impact_bps))
        adv = max(float(overrides.get("adv_shares", adv)), 1.0)
        return spread_bps, impact_bps, adv

    def _effective_slippage_pct(self, request: OrderRequest, mark_price: float) -> float:
        """Round-trip slippage used to shift mark into a fill price.

        For non-flat profiles:
        - one-way spread drag = spread_bps / 20000
        - one-way impact drag = (impact_coeff_bps * sqrt(order_qty / ADV)) / 10000
        """
        if self._cost_profile == "flat":
            return self._slippage_pct
        spread_bps, impact_coeff_bps, adv = self._cost_params_for_symbol(request.symbol)
        participation = max(request.quantity / adv, 0.0)
        impact_bps = impact_coeff_bps * math.sqrt(participation)
        one_way = (spread_bps / 20_000.0) + (impact_bps / 10_000.0)
        return max(one_way * 2.0, 0.0)

    def _execute_order(self, order_id: str, request: OrderRequest, as_of: datetime) -> OrderResult:
        """Apply slippage/cash/position checks and return FILLED/REJECTED result."""
        mark_price = self._mark_prices.get(request.symbol)
        if mark_price is None:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.REJECTED,
                request=request,
                rejection_reason=f"No mark price set for {request.symbol}",
            )
        slippage_pct = self._effective_slippage_pct(request, mark_price)
        half_slip = slippage_pct / 2

        if request.side == Side.BUY:
            fill_price = mark_price * (1 + half_slip)
            cost = fill_price * request.quantity
            if cost > self._cash:
                return OrderResult(
                    order_id=order_id,
                    status=OrderStatus.REJECTED,
                    request=request,
                    rejection_reason=f"Insufficient cash: need {cost:.2f}, have {self._cash:.2f}",
                )
            self._cash -= cost
            self._update_position(request.symbol, request.quantity, fill_price)
        else:
            fill_price = mark_price * (1 - half_slip)
            pos = self._positions.get(request.symbol)
            if pos is None or pos.quantity < request.quantity:
                held = pos.quantity if pos else 0
                return OrderResult(
                    order_id=order_id,
                    status=OrderStatus.REJECTED,
                    request=request,
                    rejection_reason=f"Insufficient shares: need {request.quantity}, have {held}",
                )
            proceeds = fill_price * request.quantity
            self._cash += proceeds
            self._update_position(request.symbol, -request.quantity, fill_price)

        fill = Fill(
            fill_id=str(uuid.uuid4()),
            order_id=order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            price=fill_price,
            slippage_pct=slippage_pct,
            filled_at=as_of,
            bot_id=request.bot_id,
        )
        self._fills.append(fill)
        return OrderResult(order_id=order_id, status=OrderStatus.FILLED, request=request, fill=fill)

    def submit_order(self, request: OrderRequest, as_of: datetime) -> OrderResult:
        order_id = str(uuid.uuid4())
        if self._execution_timing == "next_bar":
            self._pending_orders.append(_PendingOrder(order_id=order_id, request=request, submitted_at=as_of))
            return OrderResult(order_id=order_id, status=OrderStatus.PENDING, request=request)
        return self._execute_order(order_id, request, as_of)

    def _update_position(self, symbol: str, delta_qty: float, fill_price: float) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            self._positions[symbol] = Position(symbol=symbol, quantity=delta_qty, avg_cost=fill_price)
            return
        new_qty = pos.quantity + delta_qty
        if new_qty <= 0:
            del self._positions[symbol]
            return
        if delta_qty > 0:
            total_cost = pos.avg_cost * pos.quantity + fill_price * delta_qty
            pos.avg_cost = total_cost / new_qty
        pos.quantity = new_qty

    def get_portfolio(self, as_of: datetime) -> PortfolioSnapshot:
        marks = self._mark_prices
        positions: list[Position] = []
        for sym, pos in self._positions.items():
            mark = marks.get(sym, pos.avg_cost)
            mv = mark * pos.quantity
            positions.append(
                Position(
                    symbol=sym,
                    quantity=pos.quantity,
                    avg_cost=pos.avg_cost,
                    market_value=mv,
                    unrealized_pnl=(mark - pos.avg_cost) * pos.quantity,
                )
            )
        equity = self._cash + sum(p.market_value for p in positions)
        return PortfolioSnapshot(cash=self._cash, positions=positions, total_equity=equity, as_of=as_of)

    def get_fills(self) -> list[Fill]:
        return list(self._fills)

    def sync_positions(self) -> PortfolioSnapshot:
        """Restore cash + positions from the last persisted snapshot (source of truth).

        With no reconciliation source or no prior snapshot, this is a no-op that
        returns the current (fresh) portfolio — a clean start has nothing to resume.
        """
        if self._recon_store is None or self._recon_bot_id is None or self._recon_mode is None:
            return self.get_portfolio(datetime.now(timezone.utc))

        snapshot = self._recon_store.get_latest_snapshot(self._recon_bot_id, self._recon_mode)
        if snapshot is None:
            return self.get_portfolio(datetime.now(timezone.utc))

        self._cash = snapshot.cash
        self._positions = {
            p.symbol: Position(symbol=p.symbol, quantity=p.quantity, avg_cost=p.avg_cost)
            for p in snapshot.positions
        }
        return self.get_portfolio(snapshot.as_of)

    def reset(self) -> None:
        self._cash = self._initial_cash
        self._positions.clear()
        self._fills.clear()
        self._pending_orders.clear()

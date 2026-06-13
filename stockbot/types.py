"""Shared domain types used across DataFeed, Broker, and bots."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ExecutionMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Quote(BaseModel):
    symbol: str
    price: float
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    as_of: datetime


class MarketEvent(BaseModel):
    """A point-in-time market or corporate event."""

    event_id: str
    event_type: str
    symbol: str
    as_of: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class OrderRequest(BaseModel):
    symbol: str
    side: Side
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    bot_id: str = "unknown"
    thesis_id: str | None = None


class Fill(BaseModel):
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    slippage_pct: float = 0.0
    commission: float = 0.0
    filled_at: datetime
    bot_id: str = "unknown"
    # shot 12: the client-minted idempotency key, when the venue echoes it back
    # on the filled order — lets the reconciler match a venue fill to our order
    # by ref_id as a fallback when the venue_order_id was never captured.
    ref_id: str | None = None


class OrderResult(BaseModel):
    order_id: str
    status: OrderStatus
    request: OrderRequest
    fill: Fill | None = None
    rejection_reason: str | None = None


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class PortfolioSnapshot(BaseModel):
    cash: float
    positions: list[Position] = Field(default_factory=list)
    total_equity: float
    as_of: datetime

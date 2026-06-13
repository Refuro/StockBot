"""Performance metrics computed from persisted snapshots and fills (Phase 1c).

All metrics are broker-independent: read straight from the state DB after a run.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime

from stockbot.core.state.store import StateStore

# Calendar seconds per year (averaged for leap years).
SECONDS_PER_YEAR = 365.25 * 24 * 3600
DEFAULT_PERIODS_PER_YEAR = 252.0


@dataclass(frozen=True)
class EquityPoint:
    as_of: str
    cash: float
    total_equity: float


@dataclass(frozen=True)
class BacktestMetrics:
    total_return: float | None
    sharpe_ratio: float | None
    periods_per_year: float | None
    hit_rate: float | None
    max_drawdown: float
    total_slippage_cost: float
    turnover: float | None
    closed_trades: int
    fill_count: int
    snapshot_count: int
    start_equity: float | None
    end_equity: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


def load_equity_curve(store: StateStore, bot_id: str, mode: str | None = None) -> list[EquityPoint]:
    """Read the persisted equity curve from the DB (no broker required)."""
    rows = store.get_equity_curve(bot_id, mode)
    return [
        EquityPoint(as_of=r["as_of"].isoformat(), cash=r["cash"], total_equity=r["total_equity"])
        for r in rows
    ]


def period_returns(curve: list[EquityPoint]) -> list[float]:
    """Simple returns between consecutive equity snapshots."""
    returns: list[float] = []
    for prev, curr in zip(curve, curve[1:]):
        if prev.total_equity <= 0:
            continue
        returns.append((curr.total_equity - prev.total_equity) / prev.total_equity)
    return returns


def infer_periods_per_year(
    curve: list[EquityPoint],
    default: float = DEFAULT_PERIODS_PER_YEAR,
) -> float:
    """Derive annualization factor from the *actual* snapshot cadence.

    Uses the median spacing between consecutive snapshot timestamps so that daily,
    intraday, or irregular cadences each annualize correctly — instead of assuming
    252 trading days. Falls back to ``default`` when cadence can't be inferred.
    """
    if len(curve) < 3:
        return default
    times = [datetime.fromisoformat(p.as_of) for p in curve]
    gaps = [
        (b - a).total_seconds()
        for a, b in zip(times, times[1:])
        if (b - a).total_seconds() > 0
    ]
    if not gaps:
        return default
    median_gap = statistics.median(gaps)
    if median_gap <= 0:
        return default
    return SECONDS_PER_YEAR / median_gap


def total_return(curve: list[EquityPoint]) -> float | None:
    """Cumulative return from first to last persisted equity point (net of slippage)."""
    if len(curve) < 2:
        return None
    start = curve[0].total_equity
    end = curve[-1].total_equity
    if start == 0:
        return None
    return (end - start) / start


def max_drawdown(curve: list[EquityPoint]) -> float:
    """Largest peak-to-trough decline in equity, as a fraction (0.0 if none)."""
    peak = float("-inf")
    worst = 0.0
    for point in curve:
        peak = max(peak, point.total_equity)
        if peak > 0:
            drawdown = (peak - point.total_equity) / peak
            worst = max(worst, drawdown)
    return worst


def sharpe_ratio(
    returns: list[float],
    *,
    periods_per_year: float = DEFAULT_PERIODS_PER_YEAR,
    risk_free_rate: float = 0.0,
) -> float | None:
    """Annualized Sharpe from per-period returns."""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    period_rf = risk_free_rate / periods_per_year
    return ((mean - period_rf) / std) * math.sqrt(periods_per_year)


def slippage_cost_for_fill(side: str, quantity: float, price: float, slippage_pct: float) -> float:
    """Estimate slippage cost for one leg from persisted fill data."""
    half = slippage_pct / 2.0
    if side == "buy":
        mark = price / (1.0 + half) if half > -1.0 else price
        return quantity * (price - mark)
    mark = price / (1.0 - half) if half < 1.0 else price
    return quantity * (mark - price)


def total_slippage_cost(fills: list[dict]) -> float:
    return sum(
        slippage_cost_for_fill(f["side"], f["quantity"], f["price"], f["slippage_pct"]) for f in fills
    )


def round_trip_returns(fills: list[dict]) -> list[float]:
    """Match buy/sell fills FIFO per symbol and return closed-trade returns."""
    inventory: dict[str, list[tuple[float, float]]] = {}
    closed: list[float] = []
    for fill in fills:
        symbol = fill["symbol"]
        qty = float(fill["quantity"])
        price = float(fill["price"])
        if fill["side"] == "buy":
            inventory.setdefault(symbol, []).append((qty, price))
            continue
        remaining = qty
        lots = inventory.get(symbol, [])
        cost_basis = 0.0
        matched_qty = 0.0
        while remaining > 0 and lots:
            lot_qty, lot_price = lots[0]
            take = min(remaining, lot_qty)
            cost_basis += take * lot_price
            matched_qty += take
            remaining -= take
            lot_qty -= take
            if lot_qty <= 0:
                lots.pop(0)
            else:
                lots[0] = (lot_qty, lot_price)
        if matched_qty > 0:
            proceeds = matched_qty * price
            closed.append((proceeds - cost_basis) / cost_basis)
        inventory[symbol] = lots
    return closed


def hit_rate(fills: list[dict]) -> float | None:
    """Fraction of closed round trips with positive return."""
    closed = round_trip_returns(fills)
    if not closed:
        return None
    return sum(1 for r in closed if r > 0) / len(closed)


def turnover(fills: list[dict], curve: list[EquityPoint]) -> float | None:
    """Total traded notional divided by average equity."""
    if not fills or not curve:
        return None
    traded = sum(float(f["quantity"]) * float(f["price"]) for f in fills)
    avg_equity = sum(p.total_equity for p in curve) / len(curve)
    if avg_equity <= 0:
        return None
    return traded / avg_equity


def compute_backtest_metrics(
    store: StateStore,
    bot_id: str,
    mode: str | None = None,
    *,
    periods_per_year: float | None = None,
) -> BacktestMetrics:
    """Compute full backtest metrics from persisted snapshots + fills.

    When ``periods_per_year`` is not given it is inferred from the actual snapshot
    cadence (1.5c), so Sharpe is annualized correctly for daily/intraday/irregular
    data rather than assuming 252 trading days.
    """
    curve = load_equity_curve(store, bot_id, mode)
    fills = store.get_fills(bot_id, mode)
    rets = period_returns(curve)
    closed = round_trip_returns(fills)

    ppy = periods_per_year if periods_per_year is not None else infer_periods_per_year(curve)

    start_eq = curve[0].total_equity if curve else None
    end_eq = curve[-1].total_equity if curve else None

    return BacktestMetrics(
        total_return=total_return(curve),
        sharpe_ratio=sharpe_ratio(rets, periods_per_year=ppy),
        periods_per_year=ppy if len(curve) >= 2 else None,
        hit_rate=hit_rate(fills),
        max_drawdown=max_drawdown(curve),
        total_slippage_cost=total_slippage_cost(fills),
        turnover=turnover(fills, curve),
        closed_trades=len(closed),
        fill_count=len(fills),
        snapshot_count=len(curve),
        start_equity=start_eq,
        end_equity=end_eq,
    )

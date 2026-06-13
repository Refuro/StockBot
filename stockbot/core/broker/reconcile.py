"""Shot 12 — live-fill reconciliation & slippage logging (go-live gating).

Live orders fill **asynchronously**: the 5 PM tick queues/places (after
approval), but the order fills at the next regular-session open and the realized
price comes back from Robinhood *later*. The simulated settle path
(`StrategyContext.settle_pending_orders`) only handles the SimulatedBroker, so
live fills were never landing in the `fills` table — the pilot would trade blind
to the exact number it exists to learn (realized-vs-modeled slippage).

This reconciler closes that gap. For the live broker it polls realized fills
(read-only against the venue), matches each to one of our orders, and records it
into `fills` with the signed realized-vs-expected slippage.

Design guarantees (the hard rules of the shot):
- **Read-only at the venue.** It calls only `broker.get_fills()` (a poll). It
  places nothing and cancels nothing.
- **Idempotent / at-least-once.** Each recorded fill gets a deterministic
  `fill_id` derived from the venue order id (+ the venue fill id when given).
  Running the reconciler twice is a no-op: an already-recorded fill is left
  untouched. A crash mid-run can only ever re-record an identical row.
- **Partial fills** record what filled and leave the order open (`partial`)
  for the remainder; a later run that sees the completed order overwrites the
  same deterministic row and marks it `filled`.
- **Unmatched venue rows are logged and skipped, never guessed.**

Slippage sign convention (documented once, here):
    slippage_pct is signed so that POSITIVE means an ADVERSE/cost execution.
      BUY : slippage_pct = (realized - expected) / expected
            (paying ABOVE the expected price is a cost -> positive)
      SELL: slippage_pct = (expected - realized) / expected
            (receiving BELOW the expected price is a cost -> positive)
A favorable fill is negative. NOTE this conflates TWO things for the pilot:
the overnight gap (decision mark at 5 PM -> next-open fill) AND execution
slippage at the open. That is acceptable for the pilot's first measurement; a
later decomposition (and the backlog marketable-limit idea) can separate them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from stockbot.types import Fill, Side

logger = logging.getLogger(__name__)

# Refs that may still receive a fill. Terminal states (rejected/failed) and
# fully-settled ('filled') refs are still matched too (so a re-run is a clean
# no-op via the deterministic fill_id), but these are the ones worth polling for.
_OPEN_REF_STATES = ("submitted", "acked", "partial")

# Tolerance for "is this order fully filled" — venue quantities are floats.
_QTY_EPS = 1e-9


def signed_slippage_pct(side: Side, expected_price: float, realized_price: float) -> float:
    """Signed realized-vs-expected slippage; positive = adverse (a cost).

    See the module docstring for the full sign convention. Returns 0.0 when the
    expected price is missing/non-positive (slippage is then unknowable; the
    fill is still recorded so the trade is not lost).
    """
    if not expected_price or expected_price <= 0:
        return 0.0
    if side == Side.BUY:
        return (realized_price - expected_price) / expected_price
    return (expected_price - realized_price) / expected_price


@dataclass
class ReconcileSummary:
    """What one reconcile pass did (returned to the CLI / tick for logging)."""

    polled: int = 0          # venue fills seen
    recorded: int = 0        # newly written fills
    updated: int = 0         # existing fills whose data changed (e.g. partial -> full)
    unchanged: int = 0       # already on disk, identical -> left untouched (idempotency)
    partial: int = 0         # fills recorded that did not complete their order
    unmatched: int = 0       # venue rows we could not map to one of our orders
    recorded_fill_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "polled": self.polled,
            "recorded": self.recorded,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "partial": self.partial,
            "unmatched": self.unmatched,
        }


def _deterministic_fill_id(matched_key: str, venue_fill_id: str) -> str:
    """Stable id for a recorded live fill — the dedupe key.

    Keyed on the matched order's identity (venue order id, else ref_id) plus the
    venue's own fill id when it provides one. Deterministic so a re-run produces
    the SAME primary key and therefore cannot double-record.
    """
    suffix = venue_fill_id if venue_fill_id and venue_fill_id != matched_key else ""
    return f"live:{matched_key}:{suffix}" if suffix else f"live:{matched_key}"


def reconcile_live_fills(
    store: Any,
    broker: Any,
    *,
    mode: str = "live",
    broker_name: str | None = None,
    now: datetime | None = None,
) -> ReconcileSummary:
    """Poll the live broker's realized fills and record them. Read-only at venue.

    `broker` must be a live broker exposing `get_fills()` (the venue poll) and
    `is_live == True`. Refuses a non-live broker so this never runs against the
    simulated settle path (paper/backtest keep their own settle, untouched).
    """
    if not getattr(broker, "is_live", False):
        raise ValueError(
            "reconcile_live_fills requires a LIVE broker; the simulated settle "
            "path handles paper/backtest fills"
        )
    name = broker_name or getattr(broker, "broker_name", "robinhood_mcp")
    now = now or datetime.now(timezone.utc)

    venue_fills: list[Fill] = list(broker.get_fills())

    # Index our orders for matching. We include EVERY ref (any state) so a
    # re-run still matches an already-settled order and writes the identical
    # row (a true no-op). Newest-first within each key so the freshest order
    # wins if a (symbol) ever recycled an id — venue ids are unique in practice.
    refs = store.list_order_refs(broker=name)
    by_venue: dict[str, dict[str, Any]] = {}
    by_ref: dict[str, dict[str, Any]] = {}
    for r in refs:
        if r.get("venue_order_id"):
            by_venue.setdefault(str(r["venue_order_id"]), r)
        by_ref.setdefault(str(r["ref_id"]), r)

    summary = ReconcileSummary(polled=len(venue_fills))

    for fill in venue_fills:
        ref = (
            by_venue.get(str(fill.order_id))
            or (by_ref.get(str(fill.ref_id)) if fill.ref_id else None)
            or by_ref.get(str(fill.order_id))
        )
        if ref is None:
            summary.unmatched += 1
            logger.warning(
                "reconcile: unmatched venue fill order_id=%s ref_id=%s %s %s x%s @ %s "
                "— logged and skipped (never guessed)",
                fill.order_id, fill.ref_id, fill.side.value, fill.symbol,
                fill.quantity, fill.price,
            )
            continue

        matched_key = str(ref.get("venue_order_id") or ref["ref_id"])
        fill_id = _deterministic_fill_id(matched_key, str(fill.fill_id))
        expected_price = ref.get("expected_price")
        side = fill.side
        slippage = signed_slippage_pct(side, expected_price or 0.0, fill.price)

        ordered_qty = ref.get("quantity")
        is_partial = bool(
            ordered_qty is not None and float(fill.quantity) + _QTY_EPS < float(ordered_qty)
        )

        existing = store.get_fill(fill_id)
        if existing is not None and _fill_matches(existing, fill, slippage, mode):
            summary.unchanged += 1
        else:
            store.record_fill(
                fill_id=fill_id,
                order_id=matched_key,
                symbol=fill.symbol,
                side=side.value,
                quantity=fill.quantity,
                price=fill.price,
                slippage_pct=slippage,
                bot_id=str(ref.get("bot_id") or fill.bot_id),
                mode=mode,
                filled_at=fill.filled_at,
                thesis_id=ref.get("thesis_id"),
            )
            if existing is None:
                summary.recorded += 1
                summary.recorded_fill_ids.append(fill_id)
            else:
                summary.updated += 1
            if expected_price:
                logger.info(
                    "reconcile: recorded %s %s x%s @ %.4f (expected %.4f, "
                    "slippage %+.4f%%%s) ref=%s",
                    side.value, fill.symbol, fill.quantity, fill.price,
                    expected_price, slippage * 100.0,
                    ", PARTIAL" if is_partial else "", ref["ref_id"],
                )
            else:
                logger.warning(
                    "reconcile: recorded %s %s x%s @ %.4f with NO expected price "
                    "(slippage unknown) ref=%s",
                    side.value, fill.symbol, fill.quantity, fill.price, ref["ref_id"],
                )

        # Advance the order-ref lifecycle so we stop treating it as outstanding.
        new_state = "partial" if is_partial else "filled"
        if is_partial:
            summary.partial += 1
        if ref.get("state") != new_state:
            store.update_order_ref(
                ref["ref_id"], state=new_state,
                venue_order_id=str(fill.order_id) if fill.order_id else None,
            )

    logger.info("reconcile pass complete: %s", summary.as_dict())
    return summary


def _fill_matches(existing: dict[str, Any], fill: Fill, slippage: float, mode: str) -> bool:
    """True when an on-disk fill already equals what we'd write — the no-op case."""
    return (
        abs(float(existing["quantity"]) - float(fill.quantity)) <= _QTY_EPS
        and abs(float(existing["price"]) - float(fill.price)) <= 1e-9
        and abs(float(existing["slippage_pct"]) - slippage) <= 1e-12
        and existing["mode"] == mode
    )

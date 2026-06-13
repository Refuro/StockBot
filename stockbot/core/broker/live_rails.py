"""Phase 5a — safety rails shared by every LIVE broker implementation.

These rails live in the broker itself, not only in the risk engine, because
they are the last line before real money moves:

- **mode pin**: a live broker refuses to operate unless config mode == live.
- **kill switch**: a ``KILL_SWITCH`` file at the repo root (path configurable)
  rejects ALL new orders. Engage:  ``touch KILL_SWITCH``  (PowerShell:
  ``New-Item KILL_SWITCH``). Approved SELL exits may still be placed through
  the approve_orders CLI; new entries cannot.
- **per-order notional ceiling**: defaults to $50/trade (the pilot protocol).
- **manual approval**: when ``manual_approval`` is on, ``submit_order`` writes
  a ``pending_approval_orders`` row + log line instead of placing; the
  ``python -m stockbot.tools.approve_orders`` CLI lists and approves/rejects.
  This is the mechanism behind the pilot's first-10-orders rule.

The pilot protocol in the plan is law: $500 / $50-per-trade / manual approval
first 10 / pilot P&L gates nothing. Never weaken these for convenience.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from stockbot.core.state.store import StateStore
from stockbot.types import OrderRequest, OrderResult, OrderStatus

logger = logging.getLogger(__name__)

DEFAULT_MAX_ORDER_NOTIONAL_USD = 50.0
DEFAULT_KILL_SWITCH_PATH = "KILL_SWITCH"


class LiveBrokerRails:
    """Mixin holding the deterministic pre-trade rails. No LLM, no exceptions."""

    # Marker the shadow lane (5b) checks: shadow contexts must never be wired
    # to an object with is_live = True.
    is_live = True

    def __init__(
        self,
        *,
        broker_name: str,
        store: StateStore,
        mode: str,
        max_order_notional_usd: float = DEFAULT_MAX_ORDER_NOTIONAL_USD,
        manual_approval: bool = True,
        kill_switch_path: str | Path = DEFAULT_KILL_SWITCH_PATH,
    ) -> None:
        if mode != "live":
            raise ValueError(
                f"{broker_name} is a LIVE broker and refuses to construct in mode={mode!r}; "
                "live brokers only operate when config mode == live"
            )
        self._broker_name = broker_name
        self._store = store
        self._mode = mode
        self._max_order_notional = max_order_notional_usd
        self._manual_approval = manual_approval
        self._kill_switch_path = Path(kill_switch_path)

    @property
    def broker_name(self) -> str:
        return self._broker_name

    @property
    def kill_switch_engaged(self) -> bool:
        return self._kill_switch_path.exists()

    @property
    def manual_approval(self) -> bool:
        return self._manual_approval

    def _rejected(self, request: OrderRequest, reason: str) -> OrderResult:
        logger.warning("%s rejected order %s %s x%s: %s", self._broker_name,
                       request.side.value, request.symbol, request.quantity, reason)
        return OrderResult(
            order_id=str(uuid.uuid4()),
            status=OrderStatus.REJECTED,
            request=request,
            rejection_reason=reason,
        )

    def check_rails(self, request: OrderRequest, mark_price: float) -> OrderResult | None:
        """Run kill-switch + notional checks. Returns a REJECTED result or None."""
        if self.kill_switch_engaged:
            return self._rejected(
                request,
                f"KILL_SWITCH engaged ({self._kill_switch_path}) — all new orders refused",
            )
        notional = abs(request.quantity) * mark_price
        if notional > self._max_order_notional:
            return self._rejected(
                request,
                f"order notional ${notional:.2f} exceeds per-order ceiling "
                f"${self._max_order_notional:.2f}",
            )
        return None

    def queue_for_approval(
        self, request: OrderRequest, mark_price: float, as_of: datetime | None = None
    ) -> OrderResult:
        """Write the proposed order as a pending_approval row instead of placing.

        The idempotency ref_id is minted HERE and stored on the row, so the
        approval CLI re-uses it when placing — a retry after a crash mid-place
        cannot double-submit. ``mark_price`` is the decision-time reference price
        (shot 12): persisted as ``expected_price`` so it survives the approval
        queue and lands on the order ref for realized-vs-expected slippage.
        """
        approval_id = str(uuid.uuid4())
        ref_id = str(uuid.uuid4())
        ts = as_of or datetime.now(timezone.utc)
        self._store.record_pending_approval(
            approval_id=approval_id,
            broker=self._broker_name,
            bot_id=request.bot_id,
            symbol=request.symbol,
            side=request.side.value,
            quantity=request.quantity,
            order_type=request.order_type.value,
            ref_id=ref_id,
            requested_at=ts,
            limit_price=request.limit_price,
            thesis_id=request.thesis_id,
            notional_est=abs(request.quantity) * mark_price,
            expected_price=mark_price,
        )
        logger.info(
            "%s order queued for MANUAL APPROVAL (approval_id=%s): %s %s x%s ~$%.2f — "
            "run `python -m stockbot.tools.approve_orders` to review",
            self._broker_name, approval_id, request.side.value, request.symbol,
            request.quantity, abs(request.quantity) * mark_price,
        )
        return OrderResult(
            order_id=approval_id,
            status=OrderStatus.PENDING,
            request=request,
            rejection_reason=None,
        )

    def mint_order_ref(self, request: OrderRequest, expected_price: float | None = None) -> str:
        """Mint + persist an idempotency key BEFORE any venue call (crash-safe).

        ``expected_price`` (shot 12) is the decision-time mark; carried onto the
        order ref so the fill reconciler can compute slippage per order.
        """
        ref_id = str(uuid.uuid4())
        self._store.record_order_ref(
            ref_id=ref_id,
            broker=self._broker_name,
            bot_id=request.bot_id,
            symbol=request.symbol,
            side=request.side.value,
            quantity=request.quantity,
            thesis_id=request.thesis_id,
            expected_price=expected_price,
        )
        return ref_id

"""Phase 5a — manual-approval CLI for live-broker orders.

When a live broker runs with `manual_approval: true`, proposed orders land in
the `pending_approval_orders` table instead of being placed. This CLI is the
human in the loop (the pilot's first-10-orders rule):

    python -m stockbot.tools.approve_orders                 # list pending
    python -m stockbot.tools.approve_orders approve <id>    # place it
    python -m stockbot.tools.approve_orders reject <id> [--note "..."]

Kill-switch semantics: when the KILL_SWITCH file is present, approving a BUY is
refused; approving a SELL (an exit) is still allowed — you can always get out,
never deeper in.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from stockbot.config import load_config, load_env
from stockbot.core.state.store import StateStore
from stockbot.types import OrderRequest, OrderType, Side

logger = logging.getLogger(__name__)

KILL_SWITCH_PATH = Path("KILL_SWITCH")


def _build_broker(name: str, store: StateStore):
    """Construct the named live broker with manual_approval OFF (the CLI *is*
    the approval) so place goes straight through."""
    env = load_env()
    cfg = load_config()
    if name == "alpaca":
        from stockbot.core.broker.alpaca import AlpacaBroker

        return AlpacaBroker(
            store=store,
            mode="live",
            api_key=env.alpaca_api_key,
            secret_key=env.alpaca_secret_key,
            base_url=env.alpaca_base_url,
            max_order_notional_usd=cfg.live_brokers.max_order_notional_usd,
            manual_approval=False,
        )
    if name == "robinhood_mcp":
        from stockbot.core.broker.robinhood_mcp import RobinhoodMCPBroker

        return RobinhoodMCPBroker(
            store=store,
            mode="live",
            account_number=cfg.live_brokers.robinhood_account_number,
            max_order_notional_usd=cfg.live_brokers.max_order_notional_usd,
            manual_approval=False,
        )
    raise ValueError(f"unknown broker on approval row: {name!r}")


def list_pending(store: StateStore) -> int:
    rows = store.list_pending_approvals()
    if not rows:
        print("no orders pending approval")
        return 0
    print(f"{len(rows)} order(s) pending approval:")
    for r in rows:
        print(
            f"  {r['approval_id']}  [{r['broker']}]  {r['side'].upper():4s} "
            f"{r['quantity']} {r['symbol']}  ~${(r['notional_est'] or 0):.2f}  "
            f"bot={r['bot_id']}  requested={r['requested_at']}"
        )
    return 0


def decide(store: StateStore, approval_id: str, action: str, note: str | None,
           broker_factory=_build_broker) -> int:
    row = store.get_pending_approval(approval_id)
    if row is None:
        print(f"no approval row {approval_id}")
        return 1
    if row["status"] != "pending":
        print(f"approval {approval_id} already decided: {row['status']}")
        return 1

    if action == "reject":
        store.decide_pending_approval(approval_id, "rejected", note=note)
        print(f"rejected {approval_id}")
        return 0

    if KILL_SWITCH_PATH.exists() and row["side"] == "buy":
        print("KILL_SWITCH is engaged — BUY approvals are refused (exits still allowed)")
        return 1

    request = OrderRequest(
        symbol=row["symbol"],
        side=Side(row["side"]),
        quantity=float(row["quantity"]),
        order_type=OrderType(row["order_type"]),
        limit_price=row["limit_price"],
        bot_id=row["bot_id"],
        thesis_id=row["thesis_id"],
    )
    broker = broker_factory(row["broker"], store)
    # Re-use the ref_id minted when the order was queued: a crash after place
    # but before the status update cannot double-place on retry.
    ref_id = row["ref_id"]
    store.record_order_ref(
        ref_id=ref_id, broker=row["broker"], bot_id=row["bot_id"],
        symbol=row["symbol"], side=row["side"], quantity=float(row["quantity"]),
        thesis_id=row["thesis_id"],
        # shot 12: carry the decision-time mark from the queued row onto the
        # order ref so the fill reconciler computes realized-vs-expected slippage.
        expected_price=row.get("expected_price"),
    )
    store.decide_pending_approval(approval_id, "approved", note=note)
    try:
        if row["broker"] == "alpaca":
            result = broker.place_order(request, client_order_id=ref_id)
        else:
            result = broker.place_reviewed_order(request, ref_id=ref_id)
    except Exception as exc:  # noqa: BLE001 - surface, mark failed, never retry blind
        store.decide_pending_approval(approval_id, "failed", note=str(exc)[:300])
        print(f"place FAILED for {approval_id}: {exc}")
        return 1
    store.decide_pending_approval(approval_id, "placed", venue_order_id=result.order_id)
    print(f"placed {approval_id} -> venue order {result.order_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Approve/reject live orders (Phase 5a)")
    parser.add_argument("action", nargs="?", default="list", choices=["list", "approve", "reject"])
    parser.add_argument("approval_id", nargs="?")
    parser.add_argument("--note", default=None)
    parser.add_argument("--db", default=None, help="state DB path (defaults to config)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = args.db or load_config().state.db_path
    store = StateStore(db_path)

    if args.action == "list":
        return list_pending(store)
    if not args.approval_id:
        parser.error(f"{args.action} requires an approval_id")
    return decide(store, args.approval_id, args.action, args.note)


if __name__ == "__main__":
    raise SystemExit(main())

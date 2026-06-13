"""Ticket 9c — manual-approval queue end-to-end DRY RUN (no live venue order).

Proves the rail the pilot's safety depends on — the manual-approval queue + the
`approve_orders` CLI — WITHOUT touching Robinhood. A single $50 BUY is driven
through a live-mode RobinhoodMCPBroker whose MCP transport is STUBBED, so:

  - submit_order (manual_approval ON) writes a `pending_approval_orders` row and
    places NOTHING (the stub's place tool is never called);
  - `approve_orders.list_pending` lists it;
  - approving places via the (stubbed) broker, marks the row `placed`, and the
    `broker_order_refs` idempotency row goes `acked` with the reused ref_id;
  - a second order is rejected to show reject updates the row and places nothing.

It also confirms the `pending_approval_orders` / `broker_order_refs` tables
materialize in the (live-shaped) DB — the shot-4 review flagged them absent from
paper.db because they belong to the LIVE DB, which this exercise creates.

NO Robinhood call, NO real order, NO money. Pure rail validation.

    python scripts/approval_dryrun.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from stockbot.core.broker.robinhood_mcp import RobinhoodMCPBroker

# Fake accounts for the scripted MCP — this dry run never talks to Robinhood.
AGENTIC_ACCOUNT_NUMBER = "000000804"
from stockbot.core.state.store import StateStore
from stockbot.tools import approve_orders
from stockbot.types import OrderRequest, OrderStatus, OrderType, Side

DB_PATH = Path("data/approval_dryrun.db")

# Stubbed MCP results in the POST-ticket-9a (already-unwrapped) shape — the
# broker's call_tool unwraps the `data` envelope, so consumers see exactly this.
ACCOUNTS = {
    "accounts": [
        {"account_number": "000002954", "type": "margin", "agentic_allowed": False},
        {"account_number": AGENTIC_ACCOUNT_NUMBER, "type": "cash", "agentic_allowed": True},
    ]
}


class StubMCP:
    """In-process stub of the Robinhood MCP — records calls, places nothing real."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.results = {
            "get_accounts": ACCOUNTS,
            "get_equity_quotes": {"quotes": [{"symbol": "AAPL", "price": "10.00"}]},
            "review_equity_order": {"quote": {"price": "10.00"}, "alerts": []},
            "place_equity_order": {"order": {"id": "STUB-venue-order", "state": "queued"}},
        }

    def call_tool(self, name: str, arguments: dict | None = None):
        self.calls.append(name)
        return self.results[name]

    def placed(self) -> bool:
        return "place_equity_order" in self.calls


def _order(qty: float, side: Side = Side.BUY) -> OrderRequest:
    return OrderRequest(symbol="AAPL", side=side, quantity=qty,
                        order_type=OrderType.MARKET, bot_id="pilot", thesis_id="th-dryrun")


def _tables_present(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "name IN ('pending_approval_orders','broker_order_refs') ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)  # reproducible run
    store = StateStore(DB_PATH)
    now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)

    print("=" * 64)
    print("Manual-approval queue DRY RUN (live broker, STUBBED MCP — no venue)")
    print("=" * 64)

    print(f"\n[tables] live DB {DB_PATH} contains: {_tables_present(DB_PATH)}")

    # --- queue two $50 orders (manual_approval ON places nothing) -------------
    queue_mcp = StubMCP()
    broker = RobinhoodMCPBroker(
        store=store, mode="live", account_number=AGENTIC_ACCOUNT_NUMBER,
        manual_approval=True, kill_switch_path=str(DB_PATH.parent / "DRYRUN_KILL"),
        mcp_client=queue_mcp,
    )
    r1 = broker.submit_order(_order(5.0), now)   # 5 x $10 = $50 (at the ceiling)
    r2 = broker.submit_order(_order(4.0), now)   # 4 x $10 = $40
    print(f"\n[submit] order#1 status={r1.status.name}  order#2 status={r2.status.name}")
    print(f"[submit] place_equity_order called? {queue_mcp.placed()}  (must be False)")
    assert r1.status == OrderStatus.PENDING and not queue_mcp.placed()

    pending = store.list_pending_approvals()
    print(f"\n[queue] pending_approval_orders rows: {len(pending)}")
    print("[queue] approve_orders.list_pending output:")
    approve_orders.list_pending(store)

    # --- approve order#1 via the CLI (stubbed placing broker; no real venue) --
    placing_mcp = StubMCP()
    placing_broker = RobinhoodMCPBroker(
        store=store, mode="live", account_number=AGENTIC_ACCOUNT_NUMBER,
        manual_approval=False, kill_switch_path=str(DB_PATH.parent / "DRYRUN_KILL"),
        mcp_client=placing_mcp,
    )
    approve_row = pending[0]
    rc = approve_orders.decide(
        store, approve_row["approval_id"], "approve", None,
        broker_factory=lambda name, _store: placing_broker,
    )
    decided = store.get_pending_approval(approve_row["approval_id"])
    ref = store.get_order_ref(approve_row["ref_id"])
    print(f"\n[approve] rc={rc}  row.status={decided['status']}  "
          f"venue_order_id={decided['venue_order_id']}")
    print(f"[approve] reused ref_id placed? {placing_mcp.placed()}  "
          f"broker_order_refs.state={ref['state']}  ref_venue={ref['venue_order_id']}")
    assert rc == 0 and decided["status"] == "placed" and ref["state"] == "acked"

    # --- reject order#2 (places nothing) --------------------------------------
    reject_row = pending[1]
    rc = approve_orders.decide(
        store, reject_row["approval_id"], "reject", "dry-run reject",
        broker_factory=lambda *_: (_ for _ in ()).throw(AssertionError("must not build broker")),
    )
    rejected = store.get_pending_approval(reject_row["approval_id"])
    print(f"\n[reject] rc={rc}  row.status={rejected['status']}  note={rejected['note']!r}")
    assert rc == 0 and rejected["status"] == "rejected"

    print(f"\n[final] remaining pending: {len(store.list_pending_approvals())}")
    print("\nDRY RUN OK — queue + approve/reject CLI work; no venue order placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

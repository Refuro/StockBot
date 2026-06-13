"""Ticket 9c — manual-approval queue rail regression.

Confirms the two LIVE tables flagged absent from paper.db by the shot-4 review
(`pending_approval_orders`, `broker_order_refs`) materialize on a fresh state
DB, and that a queued $50 order writes the approval row while placing NOTHING.
The full approve/reject/kill-switch CLI behaviour is covered in
`tests/test_shot4_5a_brokers.py::TestApprovalCLI`; this guards the rail the
pilot's safety depends on and is mirrored by `scripts/approval_dryrun.py`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from stockbot.core.broker.robinhood_mcp import AGENTIC_ACCOUNT_NUMBER, RobinhoodMCPBroker
from stockbot.core.state.store import StateStore
from stockbot.types import OrderRequest, OrderStatus, OrderType, Side

NOW = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)


class _StubMCP:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.results = {
            "get_accounts": {"accounts": [
                {"account_number": "000002954", "type": "margin", "agentic_allowed": False},
                {"account_number": AGENTIC_ACCOUNT_NUMBER, "type": "cash", "agentic_allowed": True},
            ]},
            "get_equity_quotes": {"quotes": [{"symbol": "AAPL", "price": "10.00"}]},
        }

    def call_tool(self, name: str, arguments: dict | None = None):
        self.calls.append(name)
        return self.results[name]


def _order(qty: float) -> OrderRequest:
    return OrderRequest(symbol="AAPL", side=Side.BUY, quantity=qty,
                        order_type=OrderType.MARKET, bot_id="pilot", thesis_id="th-1")


def test_live_tables_materialize(tmp_path) -> None:
    db = tmp_path / "live.db"
    StateStore(db)
    conn = sqlite3.connect(db)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        conn.close()
    assert {"pending_approval_orders", "broker_order_refs"} <= names


def test_queued_order_writes_row_and_places_nothing(tmp_path) -> None:
    store = StateStore(tmp_path / "live.db")
    mcp = _StubMCP()
    broker = RobinhoodMCPBroker(
        store=store, mode="live", account_number=AGENTIC_ACCOUNT_NUMBER,
        manual_approval=True, kill_switch_path=str(tmp_path / "KILL_SWITCH"),
        mcp_client=mcp,
    )
    result = broker.submit_order(_order(5.0), NOW)  # 5 x $10 = $50, at the ceiling
    assert result.status == OrderStatus.PENDING
    assert "place_equity_order" not in mcp.calls
    assert "review_equity_order" not in mcp.calls
    rows = store.list_pending_approvals()
    assert len(rows) == 1
    assert rows[0]["ref_id"]  # idempotency key pre-minted at queue time
    assert rows[0]["notional_est"] == 50.0

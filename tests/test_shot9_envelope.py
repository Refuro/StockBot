"""Ticket 9a — regression tests against the REAL live `data` envelope.

The go-live blocker (reviewer-diagnosed 2026-06-13): the live MCP wraps EVERY
tool response in a ``{"data": ..., "guide": ...}`` envelope, but the broker's
reads expect the payload at the top level. The old unit tests passed because
they mocked the *un*wrapped shape — so they never caught it.

These tests drive a REAL ``MCPHTTPClient`` over an ``httpx.MockTransport`` that
returns the actual envelopes, then run the broker's real read/write parsing on
top. They assert:
  - ``call_tool`` unwraps the ``data`` envelope once, centrally, and discards
    the advisory ``guide`` sibling;
  - the account guard ACCEPTS ••••0804 and REFUSES ••••2954;
  - ``get_portfolio`` reads buying_power / equity from the envelope;
  - positions, order placement, and fills all parse the unwrapped shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from stockbot.core.broker.robinhood_mcp import (
    AGENTIC_ACCOUNT_NUMBER,
    MCPHTTPClient,
    RobinhoodMCPBroker,
    RobinhoodMCPError,
)
from stockbot.core.state.store import StateStore
from stockbot.types import OrderRequest, OrderStatus, OrderType, Side

UTC = timezone.utc
NOW = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)
DEFAULT_ACCOUNT = "000002954"  # the user's default account — must be REFUSED


# ---------------------------------------------------------------------------
# The REAL envelopes: every payload nested under "data", advisory "guide"
# sibling that must be discarded (captured shape per the handoff).
# ---------------------------------------------------------------------------

REAL_ENVELOPES: dict[str, dict] = {
    "get_accounts": {
        "data": {
            "accounts": [
                {"account_number": "000000804", "type": "cash",
                 "agentic_allowed": True, "nickname": "Agentic"},
                {"account_number": "000002954", "type": "margin",
                 "agentic_allowed": False, "nickname": ""},
            ]
        },
        "guide": "Use get_portfolio for buying power; get_accounts BP is unreliable.",
    },
    "get_portfolio": {
        "data": {"buying_power": "123.45", "total_value": "200.00", "cash": "123.45"},
        "guide": "buying_power is authoritative here.",
    },
    "get_equity_positions": {
        "data": {
            "positions": [
                {"symbol": "F", "quantity": "3", "average_cost": "12.00",
                 "market_value": "39.00"}
            ]
        },
        "guide": "positions reconcile from the venue.",
    },
    "get_equity_quotes": {
        "data": {"quotes": [{"symbol": "AAPL", "price": "10.00",
                             "last_trade_price": "10.00"}]},
        "guide": "real-time quote.",
    },
    "review_equity_order": {
        "data": {"quote": {"price": "10.00"}, "alerts": []},
        "guide": "simulation only; no money moves.",
    },
    "place_equity_order": {
        "data": {"order": {"id": "venue-abc", "state": "queued"}},
        "guide": "REAL order placed.",
    },
    "cancel_equity_order": {"data": {"ok": True}, "guide": "cancel requested."},
    "get_equity_orders": {
        "data": {
            "orders": [
                {"id": "o1", "symbol": "AAPL", "side": "buy",
                 "filled_quantity": "4", "average_price": "10.01",
                 "state": "filled", "last_transaction_at": "2026-06-13T15:00:01Z"}
            ]
        },
        "guide": "filled agentic orders.",
    },
}


class _FakeTokenStore:
    def access_token(self) -> str:
        return "fake-token"

    def refresh(self) -> dict:
        return {"access_token": "fake-token"}


def _mcp_transport(envelopes: dict[str, dict]) -> httpx.MockTransport:
    """A minimal MCP-over-HTTP server returning the real `data` envelopes."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"],
                      "result": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                headers={"mcp-session-id": "sess-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            name = payload["params"]["name"]
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"],
                      "result": {"structuredContent": envelopes[name]}},
            )
        return httpx.Response(404, json={"error": f"unhandled {method}"})

    return httpx.MockTransport(handler)


def _live_client(envelopes: dict[str, dict] | None = None) -> MCPHTTPClient:
    return MCPHTTPClient(
        "https://agent.robinhood.com/mcp/trading",
        _FakeTokenStore(),
        http_client=httpx.Client(transport=_mcp_transport(envelopes or REAL_ENVELOPES)),
    )


def _broker(tmp_path, *, account: str = AGENTIC_ACCOUNT_NUMBER,
            manual_approval: bool = True,
            envelopes: dict[str, dict] | None = None):
    store = StateStore(tmp_path / "live.db")
    broker = RobinhoodMCPBroker(
        store=store,
        mode="live",
        account_number=account,
        manual_approval=manual_approval,
        kill_switch_path=str(tmp_path / "KILL_SWITCH"),
        mcp_client=_live_client(envelopes),
    )
    return broker, store


def _order(quantity: float = 4.0, side: Side = Side.BUY) -> OrderRequest:
    return OrderRequest(symbol="AAPL", side=side, quantity=quantity,
                        order_type=OrderType.MARKET, bot_id="pilot", thesis_id="th-1")


# ---------------------------------------------------------------------------
# The central unwrap
# ---------------------------------------------------------------------------


class TestUnwrap:
    def test_unwraps_envelope_and_discards_guide(self) -> None:
        env = {"data": {"accounts": [{"account_number": "000000804"}]},
               "guide": "advisory"}
        assert MCPHTTPClient._unwrap_data_envelope(env) == {
            "accounts": [{"account_number": "000000804"}]
        }

    def test_unwraps_data_only_envelope(self) -> None:
        assert MCPHTTPClient._unwrap_data_envelope({"data": {"x": 1}}) == {"x": 1}

    def test_leaves_unwrapped_payload_untouched(self) -> None:
        # A real payload that merely *contains* a "data" field among real keys
        # is NOT the envelope and must not be stripped.
        payload = {"data": 1, "accounts": []}
        assert MCPHTTPClient._unwrap_data_envelope(payload) == payload

    def test_leaves_plain_dict_untouched(self) -> None:
        assert MCPHTTPClient._unwrap_data_envelope({"accounts": []}) == {"accounts": []}

    def test_call_tool_returns_unwrapped_payload(self) -> None:
        client = _live_client()
        result = client.call_tool("get_accounts")
        assert "data" not in result and "guide" not in result
        assert isinstance(result["accounts"], list)
        assert result["accounts"][0]["account_number"] == "000000804"


# ---------------------------------------------------------------------------
# Account guard against the real envelope
# ---------------------------------------------------------------------------


class TestAccountGuardRealShape:
    def test_accepts_agentic_0804(self, tmp_path) -> None:
        broker, _ = _broker(tmp_path, account=AGENTIC_ACCOUNT_NUMBER)
        # _verify_account must not raise; review uses the real envelope.
        review = broker.review_order(_order())
        assert review["alerts"] == []

    def test_refuses_default_2954(self, tmp_path) -> None:
        broker, _ = _broker(tmp_path, account=DEFAULT_ACCOUNT)
        with pytest.raises(RobinhoodMCPError, match="agentic_allowed"):
            broker.review_order(_order())

    def test_refuses_unknown_account(self, tmp_path) -> None:
        broker, _ = _broker(tmp_path, account="000000000")
        with pytest.raises(RobinhoodMCPError, match="not found"):
            broker.review_order(_order())


# ---------------------------------------------------------------------------
# Portfolio / positions / orders parse the unwrapped shape
# ---------------------------------------------------------------------------


class TestReadPathRealShape:
    def test_get_portfolio_reads_buying_power_and_equity(self, tmp_path) -> None:
        broker, _ = _broker(tmp_path)
        snap = broker.get_portfolio(NOW)
        assert snap.cash == pytest.approx(123.45)
        assert snap.total_equity == pytest.approx(200.00)
        assert [(p.symbol, p.quantity, p.avg_cost) for p in snap.positions] == [
            ("F", 3.0, 12.0)
        ]

    def test_positions_parse(self, tmp_path) -> None:
        broker, _ = _broker(tmp_path)
        snap = broker.sync_positions()
        assert len(snap.positions) == 1
        assert snap.positions[0].symbol == "F"

    def test_get_fills_parse(self, tmp_path) -> None:
        broker, _ = _broker(tmp_path)
        fills = broker.get_fills()
        assert len(fills) == 1
        assert fills[0].symbol == "AAPL"
        assert fills[0].price == pytest.approx(10.01)
        assert fills[0].quantity == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Order placement parses the order envelope (write path audit)
# ---------------------------------------------------------------------------


class TestWritePathRealShape:
    def test_place_reviewed_order_parses_venue_id(self, tmp_path) -> None:
        broker, store = _broker(tmp_path, manual_approval=False)
        ref_id = broker.mint_order_ref(_order())
        result = broker.place_reviewed_order(_order(), ref_id=ref_id)
        assert result.status == OrderStatus.PENDING
        assert result.order_id == "venue-abc"
        ref = store.get_order_ref(ref_id)
        assert ref["state"] == "acked"
        assert ref["venue_order_id"] == "venue-abc"

    def test_submit_order_end_to_end_uses_quote_and_places(self, tmp_path) -> None:
        broker, store = _broker(tmp_path, manual_approval=False)
        result = broker.submit_order(_order(), NOW)  # 4 x $10 = $40 < $50 ceiling
        assert result.status == OrderStatus.PENDING
        assert result.order_id == "venue-abc"

"""Ticket 5a — live broker unit tests (offline, deterministic fixtures).

Hard rules proven here, not just promised:
- account pin: the Robinhood broker refuses any non-agentic account;
- mode pin: live brokers refuse to construct outside mode=live;
- kill switch: a KILL_SWITCH file rejects all new orders;
- notional ceiling: orders above $50 are rejected in the broker itself;
- manual approval: submit_order writes a pending_approval row and calls NO
  placing tool; the approval CLI places with the SAME pre-minted ref_id;
- idempotency: the ref_id is persisted BEFORE the venue call;
- review-first: place_reviewed_order calls review_equity_order before
  place_equity_order;
- reconciliation: positions come from the venue, never from memory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from stockbot.core.broker.alpaca import AlpacaBroker
from stockbot.core.broker.robinhood_mcp import (
    AGENTIC_ACCOUNT_NUMBER,
    RobinhoodMCPBroker,
    RobinhoodMCPError,
)
from stockbot.core.state.store import StateStore
from stockbot.tools import approve_orders
from stockbot.types import OrderRequest, OrderStatus, OrderType, Side

UTC = timezone.utc
NOW = datetime(2026, 6, 12, 15, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Robinhood fixtures: a fake MCP client returning recorded tool results
# ---------------------------------------------------------------------------


ACCOUNTS_FIXTURE = {
    "accounts": [
        {"account_number": "000002954", "nickname": "", "type": "margin", "agentic_allowed": False},
        {"account_number": AGENTIC_ACCOUNT_NUMBER, "nickname": "Agentic", "type": "cash",
         "agentic_allowed": True},
    ]
}


class FakeMCPClient:
    """Recorded tool results + a call log (the offline ground truth)."""

    def __init__(self, results: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.results: dict[str, object] = {
            "get_accounts": ACCOUNTS_FIXTURE,
            "get_equity_quotes": {"quotes": [{"symbol": "AAPL", "price": "10.00"}]},
            "get_portfolio": {"buying_power": "0.00", "total_value": "0.00"},
            "get_equity_positions": {"positions": []},
            "get_equity_orders": {"orders": []},
            "review_equity_order": {"quote": {"price": "10.00"}, "alerts": []},
            "place_equity_order": {"order": {"id": "venue-123", "state": "queued"}},
            "cancel_equity_order": {"ok": True},
            **(results or {}),
        }

    def call_tool(self, name: str, arguments: dict | None = None):
        self.calls.append((name, arguments or {}))
        result = self.results[name]
        if callable(result):
            return result(arguments or {})
        return result

    def called(self, name: str) -> list[dict]:
        return [args for n, args in self.calls if n == name]


def _rh_broker(tmp_path, *, manual_approval: bool, mcp: FakeMCPClient | None = None,
               account: str = AGENTIC_ACCOUNT_NUMBER, max_notional: float = 50.0,
               kill_switch: str | None = None) -> tuple[RobinhoodMCPBroker, FakeMCPClient, StateStore]:
    store = StateStore(tmp_path / "live.db")
    mcp = mcp or FakeMCPClient()
    broker = RobinhoodMCPBroker(
        store=store,
        mode="live",
        account_number=account,
        manual_approval=manual_approval,
        max_order_notional_usd=max_notional,
        kill_switch_path=str(tmp_path / (kill_switch or "KILL_SWITCH")),
        mcp_client=mcp,
    )
    return broker, mcp, store


def _order(quantity: float = 4.0, side: Side = Side.BUY) -> OrderRequest:
    return OrderRequest(symbol="AAPL", side=side, quantity=quantity,
                        order_type=OrderType.MARKET, bot_id="pilot", thesis_id="th-1")


class TestRobinhoodRails:
    def test_refuses_construction_outside_live_mode(self, tmp_path) -> None:
        store = StateStore(tmp_path / "x.db")
        with pytest.raises(ValueError, match="refuses to construct in mode='paper'"):
            RobinhoodMCPBroker(store=store, mode="paper", mcp_client=FakeMCPClient())

    def test_refuses_non_agentic_account(self, tmp_path) -> None:
        broker, _, _ = _rh_broker(tmp_path, manual_approval=True, account="000002954")
        with pytest.raises(RobinhoodMCPError, match="agentic_allowed"):
            broker.submit_order(_order(), NOW)

    def test_refuses_unknown_account(self, tmp_path) -> None:
        broker, _, _ = _rh_broker(tmp_path, manual_approval=True, account="000000000")
        with pytest.raises(RobinhoodMCPError, match="not found"):
            broker.submit_order(_order(), NOW)

    def test_kill_switch_rejects_all_new_orders(self, tmp_path) -> None:
        broker, mcp, _ = _rh_broker(tmp_path, manual_approval=False)
        (tmp_path / "KILL_SWITCH").touch()
        result = broker.submit_order(_order(), NOW)
        assert result.status == OrderStatus.REJECTED
        assert "KILL_SWITCH" in result.rejection_reason
        sell = broker.submit_order(_order(side=Side.SELL), NOW)
        assert sell.status == OrderStatus.REJECTED  # broker path: ALL new orders
        assert not mcp.called("place_equity_order")

    def test_notional_ceiling_enforced_in_broker(self, tmp_path) -> None:
        broker, mcp, _ = _rh_broker(tmp_path, manual_approval=False)
        result = broker.submit_order(_order(quantity=6.0), NOW)  # 6 x $10 = $60 > $50
        assert result.status == OrderStatus.REJECTED
        assert "ceiling" in result.rejection_reason
        assert not mcp.called("place_equity_order")

    def test_manual_approval_queues_and_never_places(self, tmp_path) -> None:
        broker, mcp, store = _rh_broker(tmp_path, manual_approval=True)
        result = broker.submit_order(_order(), NOW)
        assert result.status == OrderStatus.PENDING
        assert not mcp.called("place_equity_order")
        assert not mcp.called("review_equity_order")
        rows = store.list_pending_approvals()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "AAPL"
        assert rows[0]["thesis_id"] == "th-1"
        assert rows[0]["ref_id"]  # idempotency key pre-minted at queue time
        assert rows[0]["notional_est"] == pytest.approx(40.0)

    def test_place_is_review_first_with_persisted_ref_id(self, tmp_path) -> None:
        broker, mcp, store = _rh_broker(tmp_path, manual_approval=False)

        # Prove ref_id row exists BEFORE place_equity_order is invoked.
        ref_state_at_place: list[str] = []
        original = mcp.results["place_equity_order"]

        def place_spy(args: dict):
            row = store.get_order_ref(args["ref_id"])
            ref_state_at_place.append(row["state"] if row else "MISSING")
            return original

        mcp.results["place_equity_order"] = place_spy
        result = broker.submit_order(_order(), NOW)
        assert result.status == OrderStatus.PENDING
        tool_sequence = [n for n, _ in mcp.calls if n in ("review_equity_order", "place_equity_order")]
        assert tool_sequence == ["review_equity_order", "place_equity_order"]
        assert ref_state_at_place == ["submitted"]  # persisted before the call
        placed_args = mcp.called("place_equity_order")[0]
        assert placed_args["account_number"] == AGENTIC_ACCOUNT_NUMBER
        ref = store.get_order_ref(placed_args["ref_id"])
        assert ref["state"] == "acked"
        assert ref["venue_order_id"] == "venue-123"

    def test_positions_reconcile_from_venue_not_memory(self, tmp_path) -> None:
        mcp = FakeMCPClient(
            {
                "get_equity_positions": {
                    "positions": [
                        {"symbol": "XYZ", "quantity": "3", "average_cost": "12.00",
                         "market_value": "39.00"}
                    ]
                },
                "get_portfolio": {"buying_power": "11.00", "total_value": "50.00"},
            }
        )
        broker, _, _ = _rh_broker(tmp_path, manual_approval=True, mcp=mcp)
        snapshot = broker.sync_positions()
        assert snapshot.cash == pytest.approx(11.0)
        assert snapshot.total_equity == pytest.approx(50.0)
        assert [(p.symbol, p.quantity, p.avg_cost) for p in snapshot.positions] == [("XYZ", 3.0, 12.0)]

    def test_review_round_trip_uses_simulation_tool_only(self, tmp_path) -> None:
        broker, mcp, _ = _rh_broker(tmp_path, manual_approval=True)
        review = broker.review_order(_order())
        assert review["alerts"] == []
        assert mcp.called("review_equity_order")
        assert not mcp.called("place_equity_order")


# ---------------------------------------------------------------------------
# Alpaca fixtures: httpx.MockTransport over recorded REST responses
# ---------------------------------------------------------------------------


def _alpaca_transport(state: dict) -> httpx.MockTransport:
    """Stateful fake of the paper API: place -> accepted; poll -> filled;
    positions reflect the fill; cancelable until filled."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "data.alpaca.markets":
            return httpx.Response(200, json={"trade": {"p": 10.0}})
        if path == "/v2/account":
            return httpx.Response(200, json={"cash": "1000.00", "equity": "1000.00"})
        if path == "/v2/orders" and request.method == "POST":
            body = json.loads(request.content)
            state["order"] = {
                "id": "alp-order-1", "client_order_id": body["client_order_id"],
                "symbol": body["symbol"], "side": body["side"], "qty": body["qty"],
                "status": "accepted", "filled_qty": "0", "filled_avg_price": None,
            }
            return httpx.Response(200, json=state["order"])
        if path == "/v2/orders/alp-order-1" and request.method == "GET":
            o = state["order"]
            state.setdefault("polls", 0)
            state["polls"] += 1
            if state["polls"] >= 2:  # fills on the second poll
                o.update(status="filled", filled_qty=o["qty"], filled_avg_price="10.01",
                         filled_at="2026-06-12T15:00:01Z")
            return httpx.Response(200, json=o)
        if path == "/v2/orders/alp-order-1" and request.method == "DELETE":
            state["cancelled"] = True
            return httpx.Response(204)
        if path == "/v2/orders" and request.method == "GET":
            o = state.get("order")
            return httpx.Response(200, json=[o] if o else [])
        if path == "/v2/positions":
            o = state.get("order")
            if o and o["status"] == "filled":
                qty = float(o["qty"])
                return httpx.Response(200, json=[{
                    "symbol": o["symbol"], "qty": o["qty"], "avg_entry_price": "10.01",
                    "market_value": f"{qty * 10.01:.2f}", "unrealized_pl": "0.00",
                }])
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})

    return httpx.MockTransport(handler)


def _alpaca_broker(tmp_path, *, manual_approval: bool, state: dict | None = None,
                   base_url: str = "https://paper-api.alpaca.markets") -> tuple[AlpacaBroker, StateStore, dict]:
    state = state if state is not None else {}
    store = StateStore(tmp_path / "alp.db")
    broker = AlpacaBroker(
        store=store, mode="live", api_key="key", secret_key="secret", base_url=base_url,
        manual_approval=manual_approval,
        kill_switch_path=str(tmp_path / "KILL_SWITCH"),
        http_client=httpx.Client(transport=_alpaca_transport(state)),
    )
    return broker, store, state


class TestAlpacaRails:
    def test_refuses_live_host_without_phase5c_flag(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="non-paper host"):
            _alpaca_broker(tmp_path, manual_approval=True, base_url="https://api.alpaca.markets")

    def test_refuses_construction_outside_live_mode(self, tmp_path) -> None:
        store = StateStore(tmp_path / "x.db")
        with pytest.raises(ValueError, match="refuses to construct"):
            AlpacaBroker(store=store, mode="backtest", api_key="k", secret_key="s")

    def test_kill_switch_and_ceiling(self, tmp_path) -> None:
        broker, _, _ = _alpaca_broker(tmp_path, manual_approval=False)
        over = broker.submit_order(_order(quantity=6.0), NOW)  # $60 > $50
        assert over.status == OrderStatus.REJECTED and "ceiling" in over.rejection_reason
        (tmp_path / "KILL_SWITCH").touch()
        killed = broker.submit_order(_order(), NOW)
        assert killed.status == OrderStatus.REJECTED and "KILL_SWITCH" in killed.rejection_reason

    def test_manual_approval_queues_instead_of_placing(self, tmp_path) -> None:
        broker, store, state = _alpaca_broker(tmp_path, manual_approval=True)
        result = broker.submit_order(_order(), NOW)
        assert result.status == OrderStatus.PENDING
        assert "order" not in state  # nothing POSTed
        assert len(store.list_pending_approvals()) == 1

    def test_full_lifecycle_place_poll_fill_reconcile_cancel(self, tmp_path) -> None:
        broker, store, state = _alpaca_broker(tmp_path, manual_approval=False)
        result = broker.submit_order(_order(), NOW)
        assert result.status == OrderStatus.PENDING
        assert state["order"]["status"] == "accepted"
        # Idempotency key persisted and acked.
        ref = store.get_order_ref(state["order"]["client_order_id"])
        assert ref["state"] == "acked" and ref["venue_order_id"] == "alp-order-1"
        # Poll until filled.
        assert broker.get_order("alp-order-1")["status"] == "accepted"
        assert broker.get_order("alp-order-1")["status"] == "filled"
        # Reconcile from the venue.
        snapshot = broker.sync_positions()
        assert [(p.symbol, p.quantity) for p in snapshot.positions] == [("AAPL", 4.0)]
        fills = broker.get_fills()
        assert len(fills) == 1 and fills[0].price == pytest.approx(10.01)
        # Cancel path (no-op once filled at the venue, but the call path works).
        broker.cancel_order("alp-order-1")
        assert state["cancelled"] is True


# ---------------------------------------------------------------------------
# Approval CLI
# ---------------------------------------------------------------------------


class TestApprovalCLI:
    def test_approve_places_with_the_preminted_ref_id(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)  # KILL_SWITCH check is repo-root relative
        broker, mcp, store = _rh_broker(tmp_path, manual_approval=True)
        broker.submit_order(_order(), NOW)
        row = store.list_pending_approvals()[0]

        placing_broker, placing_mcp, _ = _rh_broker(tmp_path, manual_approval=False, mcp=FakeMCPClient())
        # Reuse the same store so the CLI sees the queue.
        placing_broker._store = store  # noqa: SLF001 - test wiring

        rc = approve_orders.decide(
            store, row["approval_id"], "approve", None,
            broker_factory=lambda name, _store: placing_broker,
        )
        assert rc == 0
        placed = placing_mcp.called("place_equity_order")
        assert len(placed) == 1
        assert placed[0]["ref_id"] == row["ref_id"]  # the queue-time key, reused
        decided = store.get_pending_approval(row["approval_id"])
        assert decided["status"] == "placed"
        assert decided["venue_order_id"] == "venue-123"

    def test_reject_marks_row_and_places_nothing(self, tmp_path) -> None:
        broker, mcp, store = _rh_broker(tmp_path, manual_approval=True)
        broker.submit_order(_order(), NOW)
        row = store.list_pending_approvals()[0]
        rc = approve_orders.decide(store, row["approval_id"], "reject", "too rich",
                                   broker_factory=lambda *_: pytest.fail("must not build a broker"))
        assert rc == 0
        assert store.get_pending_approval(row["approval_id"])["status"] == "rejected"
        assert not mcp.called("place_equity_order")

    def test_kill_switch_blocks_buy_approval_but_not_sell(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        broker, _, store = _rh_broker(tmp_path, manual_approval=True)
        broker.submit_order(_order(), NOW)
        broker.submit_order(_order(side=Side.SELL), NOW)
        buy_row = next(r for r in store.list_pending_approvals() if r["side"] == "buy")
        sell_row = next(r for r in store.list_pending_approvals() if r["side"] == "sell")

        (tmp_path / "KILL_SWITCH").touch()
        rc = approve_orders.decide(store, buy_row["approval_id"], "approve", None,
                                   broker_factory=lambda *_: pytest.fail("buy must be blocked"))
        assert rc == 1  # buy refused under kill switch
        assert store.get_pending_approval(buy_row["approval_id"])["status"] == "pending"

        placing_broker, placing_mcp, _ = _rh_broker(tmp_path, manual_approval=False)
        placing_broker._store = store  # noqa: SLF001 - test wiring
        rc = approve_orders.decide(store, sell_row["approval_id"], "approve", None,
                                   broker_factory=lambda name, _s: placing_broker)
        assert rc == 0  # exits still allowed
        assert placing_mcp.called("place_equity_order")

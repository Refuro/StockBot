"""Shot 12 — live-fill reconciliation & slippage logging.

Proves the go-live gating behaviour with a stubbed MCP (offline, deterministic):

- expected_price is carried decision -> order ref -> fill (queue + auto-place);
- the reconciler records realized price/time + CORRECT SIGNED slippage vs the
  expected price (BUY pays above = positive; SELL receives below = positive);
- idempotency: a second reconcile pass is a no-op (no duplicate fills);
- partial fills are recorded and leave the order open ('partial');
- unmatched venue rows are logged + skipped, never guessed;
- the on-tick path and the standalone CLI land identical rows;
- zero orders are placed by the reconciler (read-only at the venue).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stockbot.core.broker.reconcile import (
    reconcile_live_fills,
    signed_slippage_pct,
)
from stockbot.core.broker.robinhood_mcp import AGENTIC_ACCOUNT_NUMBER, RobinhoodMCPBroker
from stockbot.core.state.store import StateStore
from stockbot.types import OrderRequest, OrderType, Side

UTC = timezone.utc
NOW = datetime(2026, 6, 13, 21, 0, tzinfo=UTC)  # a 5 PM ET tick


# ---------------------------------------------------------------------------
# A stubbed MCP that returns recorded venue results + a call log.
# ---------------------------------------------------------------------------


class StubMCP:
    """Records placing tool calls; serves get_equity_orders for fill polls."""

    def __init__(self, filled_orders: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._filled = filled_orders or []
        self.results: dict[str, object] = {
            "get_accounts": {
                "accounts": [
                    {"account_number": "000002954", "type": "margin", "agentic_allowed": False},
                    {"account_number": AGENTIC_ACCOUNT_NUMBER, "type": "cash",
                     "agentic_allowed": True},
                ]
            },
            "get_equity_quotes": {"quotes": [{"symbol": "AAPL", "price": "10.00"}]},
            "review_equity_order": {"quote": {"price": "10.00"}, "alerts": []},
            "place_equity_order": {"order": {"id": "venue-1", "state": "queued"}},
        }

    def set_filled(self, orders: list[dict]) -> None:
        self._filled = orders

    def call_tool(self, name: str, arguments: dict | None = None):
        self.calls.append((name, arguments or {}))
        if name == "get_equity_orders":
            return {"orders": self._filled}
        return self.results[name]

    def called(self, name: str) -> list[dict]:
        return [args for n, args in self.calls if n == name]


def _broker(store: StateStore, mcp: StubMCP, tmp_path) -> RobinhoodMCPBroker:
    return RobinhoodMCPBroker(
        store=store, mode="live", account_number=AGENTIC_ACCOUNT_NUMBER,
        manual_approval=True, kill_switch_path=str(tmp_path / "KILL_SWITCH"),
        mcp_client=mcp,
    )


def _venue_order(order_id: str, side: str, qty: float, avg_price: float,
                 *, filled_qty: float | None = None) -> dict:
    return {
        "id": order_id,
        "symbol": "AAPL",
        "side": side,
        "filled_quantity": str(filled_qty if filled_qty is not None else qty),
        "quantity": str(qty),
        "average_price": str(avg_price),
        "last_transaction_at": "2026-06-16T13:31:00Z",  # next-session open
        "placed_agent": "agentic",
    }


def _seed_acked_order(
    store: StateStore, *, ref_id: str, venue_order_id: str, side: str,
    quantity: float, expected_price: float, bot_id: str = "pilot",
    thesis_id: str = "th-1",
) -> None:
    """Mimic a placed-and-acked live order with its decision-time expected price."""
    store.record_order_ref(
        ref_id=ref_id, broker="robinhood_mcp", bot_id=bot_id, symbol="AAPL",
        side=side, quantity=quantity, thesis_id=thesis_id, expected_price=expected_price,
    )
    store.update_order_ref(ref_id, state="acked", venue_order_id=venue_order_id)


# ---------------------------------------------------------------------------
# Sign convention
# ---------------------------------------------------------------------------


class TestSignConvention:
    def test_buy_above_expected_is_positive_cost(self) -> None:
        # paid 10.20 vs expected 10.00 -> +2% adverse
        assert signed_slippage_pct(Side.BUY, 10.0, 10.20) == pytest.approx(0.02)

    def test_buy_below_expected_is_negative_favorable(self) -> None:
        assert signed_slippage_pct(Side.BUY, 10.0, 9.90) == pytest.approx(-0.01)

    def test_sell_below_expected_is_positive_cost(self) -> None:
        # received 9.80 vs expected 10.00 -> +2% adverse
        assert signed_slippage_pct(Side.SELL, 10.0, 9.80) == pytest.approx(0.02)

    def test_sell_above_expected_is_negative_favorable(self) -> None:
        assert signed_slippage_pct(Side.SELL, 10.0, 10.10) == pytest.approx(-0.01)

    def test_missing_expected_yields_zero(self) -> None:
        assert signed_slippage_pct(Side.BUY, 0.0, 10.0) == 0.0


# ---------------------------------------------------------------------------
# Expected-price plumbing
# ---------------------------------------------------------------------------


class TestExpectedPricePlumbing:
    def test_queue_persists_expected_price(self, tmp_path) -> None:
        store = StateStore(tmp_path / "live.db")
        mcp = StubMCP()
        broker = _broker(store, mcp, tmp_path)
        broker.submit_order(
            OrderRequest(symbol="AAPL", side=Side.BUY, quantity=4.0,
                         order_type=OrderType.MARKET, bot_id="pilot", thesis_id="th-1"),
            NOW,
        )
        row = store.list_pending_approvals()[0]
        assert row["expected_price"] == pytest.approx(10.0)  # the live quote at decision

    def test_approval_carries_expected_to_order_ref(self, tmp_path, monkeypatch) -> None:
        from stockbot.tools import approve_orders

        monkeypatch.chdir(tmp_path)
        store = StateStore(tmp_path / "live.db")
        mcp = StubMCP()
        broker = _broker(store, mcp, tmp_path)
        broker.submit_order(
            OrderRequest(symbol="AAPL", side=Side.BUY, quantity=4.0,
                         order_type=OrderType.MARKET, bot_id="pilot", thesis_id="th-1"),
            NOW,
        )
        row = store.list_pending_approvals()[0]
        placing = _broker(store, StubMCP(), tmp_path)
        placing._manual_approval = False  # noqa: SLF001 - CLI is the approval
        approve_orders.decide(
            store, row["approval_id"], "approve", None,
            broker_factory=lambda *_: placing,
        )
        ref = store.get_order_ref(row["ref_id"])
        assert ref["expected_price"] == pytest.approx(10.0)
        assert ref["state"] == "acked"


# ---------------------------------------------------------------------------
# The reconciler
# ---------------------------------------------------------------------------


class TestReconciler:
    def test_records_realized_price_time_and_signed_slippage(self, tmp_path) -> None:
        store = StateStore(tmp_path / "live.db")
        # A BUY whose decision mark was 10.00, filled next-open at 10.20.
        _seed_acked_order(store, ref_id="r-buy", venue_order_id="venue-buy",
                          side="buy", quantity=4.0, expected_price=10.0)
        mcp = StubMCP([_venue_order("venue-buy", "buy", 4.0, 10.20)])
        broker = _broker(store, mcp, tmp_path)

        summary = reconcile_live_fills(store, broker)
        assert summary.recorded == 1 and summary.unmatched == 0

        fills = store.get_fills("pilot", "live")
        assert len(fills) == 1
        f = fills[0]
        assert f["price"] == pytest.approx(10.20)
        assert f["slippage_pct"] == pytest.approx(0.02)  # +2% adverse
        assert f["thesis_id"] == "th-1"
        assert f["filled_at"] == datetime(2026, 6, 16, 13, 31, tzinfo=UTC)
        # The order ref is now settled so we stop treating it as outstanding.
        assert store.get_order_ref("r-buy")["state"] == "filled"
        # READ-ONLY: nothing was placed/cancelled.
        assert not mcp.called("place_equity_order")
        assert not mcp.called("cancel_equity_order")

    def test_sell_slippage_sign(self, tmp_path) -> None:
        store = StateStore(tmp_path / "live.db")
        _seed_acked_order(store, ref_id="r-sell", venue_order_id="venue-sell",
                          side="sell", quantity=3.0, expected_price=10.0)
        mcp = StubMCP([_venue_order("venue-sell", "sell", 3.0, 9.80)])
        broker = _broker(store, mcp, tmp_path)
        reconcile_live_fills(store, broker)
        f = store.get_fills("pilot", "live")[0]
        assert f["slippage_pct"] == pytest.approx(0.02)  # received below expected = cost

    def test_idempotent_second_pass_is_noop(self, tmp_path) -> None:
        store = StateStore(tmp_path / "live.db")
        _seed_acked_order(store, ref_id="r-buy", venue_order_id="venue-buy",
                          side="buy", quantity=4.0, expected_price=10.0)
        mcp = StubMCP([_venue_order("venue-buy", "buy", 4.0, 10.20)])
        broker = _broker(store, mcp, tmp_path)

        first = reconcile_live_fills(store, broker)
        assert first.recorded == 1
        after_first = store.get_fills("pilot", "live")

        second = reconcile_live_fills(store, broker)
        assert second.recorded == 0 and second.updated == 0
        assert second.unchanged == 1
        after_second = store.get_fills("pilot", "live")
        # Exactly the same single row — no double-record.
        assert len(after_second) == 1
        assert [r["fill_id"] for r in after_first] == [r["fill_id"] for r in after_second]
        assert after_first[0]["price"] == after_second[0]["price"]

    def test_partial_fill_recorded_and_order_left_open(self, tmp_path) -> None:
        store = StateStore(tmp_path / "live.db")
        _seed_acked_order(store, ref_id="r-buy", venue_order_id="venue-buy",
                          side="buy", quantity=4.0, expected_price=10.0)
        # Only 1 of 4 shares filled so far.
        mcp = StubMCP([_venue_order("venue-buy", "buy", 4.0, 10.10, filled_qty=1.0)])
        broker = _broker(store, mcp, tmp_path)

        summary = reconcile_live_fills(store, broker)
        assert summary.partial == 1
        f = store.get_fills("pilot", "live")[0]
        assert f["quantity"] == pytest.approx(1.0)
        # Order stays OPEN for the remainder.
        assert store.get_order_ref("r-buy")["state"] == "partial"

        # Later the order completes: same deterministic row, now full + 'filled'.
        mcp.set_filled([_venue_order("venue-buy", "buy", 4.0, 10.15, filled_qty=4.0)])
        summary2 = reconcile_live_fills(store, broker)
        assert summary2.updated == 1 and summary2.recorded == 0
        fills = store.get_fills("pilot", "live")
        assert len(fills) == 1  # overwritten, not duplicated
        assert fills[0]["quantity"] == pytest.approx(4.0)
        assert store.get_order_ref("r-buy")["state"] == "filled"

    def test_unmatched_venue_row_logged_and_skipped(self, tmp_path) -> None:
        store = StateStore(tmp_path / "live.db")
        # No order ref seeded -> the venue fill matches nothing.
        mcp = StubMCP([_venue_order("venue-ghost", "buy", 2.0, 10.0)])
        broker = _broker(store, mcp, tmp_path)
        summary = reconcile_live_fills(store, broker)
        assert summary.unmatched == 1 and summary.recorded == 0
        assert store.get_fills("pilot", "live") == []

    def test_match_by_ref_id_when_venue_id_absent(self, tmp_path) -> None:
        store = StateStore(tmp_path / "live.db")
        # Order ref exists but never captured its venue_order_id (crash mid-ack).
        store.record_order_ref(
            ref_id="ref-xyz", broker="robinhood_mcp", bot_id="pilot", symbol="AAPL",
            side="buy", quantity=2.0, thesis_id="th-1", expected_price=10.0,
        )
        store.update_order_ref("ref-xyz", state="submitted")
        # The venue echoes our client ref_id on the filled order.
        order = _venue_order("venue-late", "buy", 2.0, 10.05)
        order["ref_id"] = "ref-xyz"
        mcp = StubMCP([order])
        broker = _broker(store, mcp, tmp_path)
        summary = reconcile_live_fills(store, broker)
        assert summary.recorded == 1 and summary.unmatched == 0
        assert store.get_fills("pilot", "live")[0]["slippage_pct"] == pytest.approx(0.005)

    def test_refuses_non_live_broker(self, tmp_path) -> None:
        from stockbot.core.broker.simulated import SimulatedBroker

        store = StateStore(tmp_path / "live.db")
        with pytest.raises(ValueError, match="LIVE broker"):
            reconcile_live_fills(store, SimulatedBroker())


# ---------------------------------------------------------------------------
# On-tick path == CLI path (identical rows)
# ---------------------------------------------------------------------------


class TestTickAndCliParity:
    def test_context_reconcile_matches_direct_call(self, tmp_path) -> None:
        from types import SimpleNamespace

        from stockbot.core.strategy import StrategyContext

        store = StateStore(tmp_path / "live.db")
        _seed_acked_order(store, ref_id="r-buy", venue_order_id="venue-buy",
                          side="buy", quantity=4.0, expected_price=10.0)
        mcp = StubMCP([_venue_order("venue-buy", "buy", 4.0, 10.20)])
        broker = _broker(store, mcp, tmp_path)

        cfg = SimpleNamespace(mode=SimpleNamespace(value="live"))
        ctx = StrategyContext.__new__(StrategyContext)
        ctx.config = cfg
        ctx.broker = broker
        ctx.store = store
        ctx.run_context = None
        ctx.shadow = False

        summary = ctx.reconcile_live_fills()
        assert summary is not None and summary.recorded == 1
        rows = store.get_fills("pilot", "live")
        assert len(rows) == 1
        # Same deterministic id the standalone CLI would produce (RH's fill id
        # equals the order id, so the redundant suffix is collapsed).
        assert rows[0]["fill_id"] == "live:venue-buy"

    def test_simulated_broker_context_is_noop(self, tmp_path) -> None:
        from types import SimpleNamespace

        from stockbot.core.broker.simulated import SimulatedBroker
        from stockbot.core.strategy import StrategyContext

        store = StateStore(tmp_path / "live.db")
        ctx = StrategyContext.__new__(StrategyContext)
        ctx.config = SimpleNamespace(mode=SimpleNamespace(value="paper"))
        ctx.broker = SimulatedBroker()
        ctx.store = store
        ctx.run_context = None
        ctx.shadow = False
        assert ctx.reconcile_live_fills() is None

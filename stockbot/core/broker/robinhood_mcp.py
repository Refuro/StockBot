"""Phase 5a — RobinhoodMCPBroker: live Broker over Robinhood's agentic MCP endpoint.

Speaks MCP-over-HTTP (JSON-RPC 2.0, streamable-HTTP transport) directly to
``https://agent.robinhood.com/mcp/trading`` via httpx — no AI client involved.
Tool names/semantics per docs/robinhood-mcp-reference.md (captured live).

Safety design (the rails live HERE, not only in the risk engine):
- hard account pin: refuses to start unless the configured account exists in
  ``get_accounts`` AND has ``agentic_allowed == true`` — the user's default
  account is rejected client-side before Robinhood's own server-side gate.
- mode pin, kill switch, $50 notional ceiling, manual approval: LiveBrokerRails.
- idempotency: every place call carries a client-minted ``ref_id`` (UUID)
  persisted to ``broker_order_refs`` BEFORE the venue call; a retry after a
  crash re-sends the SAME ref_id and the venue dedupes.
- review-first: ``submit_order`` runs ``review_equity_order`` (pure simulation)
  before any ``place_equity_order``, per the platform contract.
- positions/cash reconcile from the venue (0.1b pattern), never from memory.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from stockbot.core.broker.base import Broker
from stockbot.core.broker.live_rails import LiveBrokerRails
from stockbot.core.broker.rh_oauth import DEFAULT_MCP_URL, RobinhoodTokenStore
from stockbot.core.state.store import StateStore
from stockbot.types import (
    Fill,
    OrderRequest,
    OrderResult,
    OrderStatus,
    PortfolioSnapshot,
    Position,
    Side,
)

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"

# The sandboxed agentic pilot account (cash, agentic_allowed=true). Supplied via
# ROBINHOOD_ACCOUNT_NUMBER (.env, never committed). Unset → "" → _verify_account
# refuses outright; when set, the agentic_allowed check still runs against the
# live get_accounts response, so a wrong number can never reach a non-agentic acct.
AGENTIC_ACCOUNT_NUMBER = os.environ.get("ROBINHOOD_ACCOUNT_NUMBER", "")


class RobinhoodMCPError(RuntimeError):
    """A tool call failed (transport, JSON-RPC error, or isError result)."""


class MCPHTTPClient:
    """Minimal MCP streamable-HTTP client: initialize once, then tools/call."""

    def __init__(
        self,
        url: str,
        token_store: RobinhoodTokenStore,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._tokens = token_store
        self._http = http_client or httpx.Client(timeout=30.0)
        self._session_id: str | None = None
        self._next_id = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    @staticmethod
    def _parse_body(resp: httpx.Response) -> dict[str, Any]:
        """JSON or SSE body → the JSON-RPC response object."""
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
            raise RobinhoodMCPError("SSE response contained no data event")
        return resp.json()

    def _post(self, payload: dict[str, Any], *, retry_auth: bool = True) -> dict[str, Any] | None:
        resp = self._http.post(self._url, json=payload, headers=self._headers())
        if resp.status_code == 401 and retry_auth:
            # Token died early (clock skew / revocation): one refresh, one retry.
            self._tokens.refresh()
            return self._post(payload, retry_auth=False)
        if resp.status_code == 404 and self._session_id:
            # Session expired server-side: re-initialize once and retry.
            self._session_id = None
            self.initialize()
            return self._post(payload, retry_auth=False)
        resp.raise_for_status()
        if "mcp-session-id" in resp.headers:
            self._session_id = resp.headers["mcp-session-id"]
        if resp.status_code == 202 or not resp.content:
            return None  # accepted notification
        return self._parse_body(resp)

    def initialize(self) -> None:
        self._next_id += 1
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "stockbot-robinhood-broker", "version": "0.1"},
                },
            }
        )
        if result is None or "error" in result:
            raise RobinhoodMCPError(f"MCP initialize failed: {result}")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @staticmethod
    def _unwrap_data_envelope(payload: Any) -> Any:
        """Unwrap the live MCP ``data`` envelope, ONCE, centrally (ticket 9a).

        Every live tool response wraps its real payload in a ``data`` envelope
        with an advisory ``guide`` sibling::

            {"data": {"accounts": [...]}, "guide": "..."}

        The broker's reads (``_verify_account``, ``get_portfolio``,
        ``_venue_positions``, order/fill parsing) all expect the payload at the
        top level (``.get("accounts")``, ``.get("buying_power")``, ...). Unwrap
        here so every consumer keeps working unchanged; the ``guide`` advisory
        text is discarded. Only the genuine envelope shape is unwrapped (a dict
        whose keys are a subset of ``{"data", "guide"}``) so a real payload that
        happens to contain a ``data`` field is never mis-stripped.
        """
        if (
            isinstance(payload, dict)
            and "data" in payload
            and set(payload.keys()) <= {"data", "guide"}
        ):
            return payload["data"]
        return payload

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """tools/call → parsed, data-envelope-unwrapped result.

        Prefers ``structuredContent``, else the first text content parsed as
        JSON, else the raw text. The live ``data`` envelope is unwrapped once
        here (see ``_unwrap_data_envelope``) so callers parse the real payload.
        """
        if self._session_id is None:
            self.initialize()
        self._next_id += 1
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        if response is None:
            raise RobinhoodMCPError(f"{name}: empty response")
        if "error" in response:
            raise RobinhoodMCPError(f"{name}: JSON-RPC error {response['error']}")
        result = response.get("result", {})
        if result.get("isError"):
            raise RobinhoodMCPError(f"{name}: tool error {result.get('content')}")
        if "structuredContent" in result:
            return self._unwrap_data_envelope(result["structuredContent"])
        for item in result.get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "")
                try:
                    return self._unwrap_data_envelope(json.loads(text))
                except (json.JSONDecodeError, TypeError):
                    return text
        return self._unwrap_data_envelope(result)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class RobinhoodMCPBroker(LiveBrokerRails, Broker):
    """Live Robinhood broker behind the existing Broker interface (5a)."""

    def __init__(
        self,
        *,
        store: StateStore,
        mode: str,
        account_number: str = AGENTIC_ACCOUNT_NUMBER,
        mcp_url: str = DEFAULT_MCP_URL,
        max_order_notional_usd: float = 50.0,
        manual_approval: bool = True,
        kill_switch_path: str = "KILL_SWITCH",
        mcp_client: MCPHTTPClient | None = None,
        token_store: RobinhoodTokenStore | None = None,
    ) -> None:
        LiveBrokerRails.__init__(
            self,
            broker_name="robinhood_mcp",
            store=store,
            mode=mode,
            max_order_notional_usd=max_order_notional_usd,
            manual_approval=manual_approval,
            kill_switch_path=kill_switch_path,
        )
        self._account_number = account_number
        self._mcp = mcp_client or MCPHTTPClient(mcp_url, token_store or RobinhoodTokenStore())
        self._account_verified = False
        self._session_fills: list[Fill] = []

    # --- account guard ------------------------------------------------------

    def _verify_account(self) -> None:
        """Refuse to operate on anything but the configured AGENTIC account."""
        if self._account_verified:
            return
        if not self._account_number:
            raise RobinhoodMCPError(
                "no agentic account configured — set ROBINHOOD_ACCOUNT_NUMBER in .env; refusing"
            )
        accounts = self._mcp.call_tool("get_accounts")
        rows = accounts.get("accounts", accounts) if isinstance(accounts, dict) else accounts
        if not isinstance(rows, list):
            rows = [rows]
        match = next(
            (a for a in rows if str(a.get("account_number")) == self._account_number), None
        )
        if match is None:
            raise RobinhoodMCPError(
                f"configured account {self._account_number} not found in get_accounts — refusing"
            )
        allowed = match.get("agentic_allowed")
        if not (allowed is True or str(allowed).lower() == "true"):
            raise RobinhoodMCPError(
                f"account {self._account_number} has agentic_allowed={allowed!r} — "
                "this broker only operates on the agentic account; refusing"
            )
        self._account_verified = True
        logger.info("robinhood_mcp: verified agentic account %s", self._account_number)

    # --- order path -----------------------------------------------------------

    def _order_args(self, request: OrderRequest) -> dict[str, Any]:
        args: dict[str, Any] = {
            "account_number": self._account_number,
            "symbol": request.symbol,
            "side": request.side.value,
            "type": request.order_type.value,
            "quantity": str(request.quantity),
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        }
        if request.limit_price is not None:
            args["limit_price"] = str(request.limit_price)
        return args

    def review_order(self, request: OrderRequest) -> Any:
        """review_equity_order — pure simulation, returns quote + pre-trade alerts."""
        self._verify_account()
        return self._mcp.call_tool("review_equity_order", self._order_args(request))

    def submit_order(self, request: OrderRequest, as_of: datetime) -> OrderResult:
        self._verify_account()

        # Mark for the notional ceiling: live quote at order time.
        quotes = self._mcp.call_tool("get_equity_quotes", {"symbols": [request.symbol]})
        quote_rows = quotes.get("quotes", quotes) if isinstance(quotes, dict) else quotes
        if not isinstance(quote_rows, list):
            quote_rows = [quote_rows]
        mark = _as_float(quote_rows[0].get("price") or quote_rows[0].get("last_trade_price")) \
            if quote_rows else 0.0
        if mark <= 0:
            return self._rejected(request, f"no live quote for {request.symbol}")

        rejected = self.check_rails(request, mark)
        if rejected is not None:
            return rejected

        if self.manual_approval:
            return self.queue_for_approval(request, mark, as_of)

        return self.place_reviewed_order(
            request, ref_id=self.mint_order_ref(request, expected_price=mark)
        )

    def place_reviewed_order(self, request: OrderRequest, *, ref_id: str) -> OrderResult:
        """review → place with a persisted ref_id. REAL MONEY when it fills.

        Shot-4 note: nothing reaches this in production this shot — manual
        approval is ON and the agentic account is $0 — but the path is the one
        the pilot will use, so it is built and unit-tested now.
        """
        self._verify_account()
        review = self._mcp.call_tool("review_equity_order", self._order_args(request))
        logger.info("robinhood_mcp review for %s %s: %s", request.side.value, request.symbol,
                    str(review)[:500])
        self._store.update_order_ref(ref_id, state="submitted")
        try:
            placed = self._mcp.call_tool(
                "place_equity_order", {**self._order_args(request), "ref_id": ref_id}
            )
        except Exception:
            self._store.update_order_ref(ref_id, state="failed")
            raise
        order = placed.get("order", placed) if isinstance(placed, dict) else {}
        venue_order_id = str(order.get("id") or order.get("order_id") or "")
        self._store.update_order_ref(ref_id, state="acked", venue_order_id=venue_order_id or None)
        logger.info("robinhood_mcp placed order %s (ref_id=%s)", venue_order_id, ref_id)
        return OrderResult(
            order_id=venue_order_id or ref_id,
            status=OrderStatus.PENDING,  # fills are discovered via get_equity_orders
            request=request,
        )

    def cancel_order(self, order_id: str) -> Any:
        self._verify_account()
        return self._mcp.call_tool(
            "cancel_equity_order",
            {"account_number": self._account_number, "order_id": order_id},
        )

    # --- portfolio / reconciliation ------------------------------------------

    def get_portfolio(self, as_of: datetime) -> PortfolioSnapshot:
        self._verify_account()
        portfolio = self._mcp.call_tool(
            "get_portfolio", {"account_number": self._account_number}
        )
        positions = self._venue_positions()
        # get_portfolio is authoritative for buying power/equity (the guide says
        # get_accounts' buying power is unreliable).
        cash = _as_float(
            portfolio.get("buying_power")
            or portfolio.get("cash")
            or portfolio.get("cash_balance")
        )
        equity = _as_float(
            portfolio.get("total_value") or portfolio.get("equity") or portfolio.get("market_value"),
            default=cash + sum(p.market_value for p in positions),
        )
        return PortfolioSnapshot(
            cash=cash, positions=positions, total_equity=equity,
            as_of=as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc),
        )

    def _venue_positions(self) -> list[Position]:
        raw = self._mcp.call_tool(
            "get_equity_positions", {"account_number": self._account_number}
        )
        rows = raw.get("positions", raw) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            rows = []
        out: list[Position] = []
        for r in rows:
            qty = _as_float(r.get("quantity"))
            if qty == 0:
                continue
            avg = _as_float(r.get("average_cost") or r.get("avg_cost"))
            mv = _as_float(r.get("market_value"), default=qty * avg)
            out.append(
                Position(
                    symbol=str(r.get("symbol", "")),
                    quantity=qty,
                    avg_cost=avg,
                    market_value=mv,
                    unrealized_pnl=mv - qty * avg,
                )
            )
        return out

    def sync_positions(self) -> PortfolioSnapshot:
        """Authoritative state from the venue — never from memory (0.1b)."""
        return self.get_portfolio(datetime.now(timezone.utc))

    # --- fills ----------------------------------------------------------------

    def get_fills(self) -> list[Fill]:
        """Filled agentic orders from the venue, mapped to our Fill type."""
        self._verify_account()
        raw = self._mcp.call_tool(
            "get_equity_orders",
            {
                "account_number": self._account_number,
                "state": "filled",
                "placed_agent": "agentic",
            },
        )
        rows = raw.get("orders", raw) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            rows = []
        fills: list[Fill] = []
        for r in rows:
            qty = _as_float(r.get("filled_quantity") or r.get("quantity"))
            price = _as_float(r.get("average_price") or r.get("price"))
            if qty <= 0 or price <= 0:
                continue
            filled_at_raw = r.get("last_transaction_at") or r.get("updated_at") or r.get("created_at")
            try:
                filled_at = datetime.fromisoformat(str(filled_at_raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                filled_at = datetime.now(timezone.utc)
            ref_id = r.get("ref_id") or r.get("client_order_id")
            fills.append(
                Fill(
                    fill_id=str(r.get("id") or uuid.uuid4()),
                    order_id=str(r.get("id") or ""),
                    symbol=str(r.get("symbol", "")),
                    side=Side(str(r.get("side", "buy"))),
                    quantity=qty,
                    price=price,
                    filled_at=filled_at,
                    ref_id=str(ref_id) if ref_id else None,
                )
            )
        return fills

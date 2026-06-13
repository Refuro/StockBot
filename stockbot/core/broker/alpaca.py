"""Phase 5a — AlpacaBroker: the hot spare, REST against ALPACA_BASE_URL.

Same `Broker` interface and the same LiveBrokerRails (mode pin, kill switch,
$50 ceiling, manual approval) as the Robinhood implementation. Plain key auth.

Shot-4 hard rule: orders go only to the *paper* host. The broker refuses any
base URL that is not the paper API unless `allow_live_host=True` is passed
explicitly — that flag is the Phase-5c config change, nothing earlier.

Idempotency: every order carries a client-minted `client_order_id` persisted
to `broker_order_refs` BEFORE the POST (Alpaca dedupes on it), mirroring the
Robinhood ref_id pattern.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from stockbot.core.broker.base import Broker
from stockbot.core.broker.live_rails import LiveBrokerRails
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

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class AlpacaBroker(LiveBrokerRails, Broker):
    """Alpaca trading API behind the existing Broker interface (5a)."""

    def __init__(
        self,
        *,
        store: StateStore,
        mode: str,
        api_key: str,
        secret_key: str,
        base_url: str = PAPER_BASE_URL,
        data_url: str = DATA_BASE_URL,
        max_order_notional_usd: float = 50.0,
        manual_approval: bool = True,
        kill_switch_path: str = "KILL_SWITCH",
        allow_live_host: bool = False,
        http_client: httpx.Client | None = None,
    ) -> None:
        LiveBrokerRails.__init__(
            self,
            broker_name="alpaca",
            store=store,
            mode=mode,
            max_order_notional_usd=max_order_notional_usd,
            manual_approval=manual_approval,
            kill_switch_path=kill_switch_path,
        )
        base_url = base_url.rstrip("/")
        if base_url.endswith("/v2"):  # tolerate ALPACA_BASE_URL with the API version baked in
            base_url = base_url[: -len("/v2")]
        if "paper" not in base_url and not allow_live_host:
            raise ValueError(
                f"AlpacaBroker refuses non-paper host {base_url!r} — switching to the "
                "live host is a Phase-5c config change (allow_live_host=True)"
            )
        if not api_key or not secret_key:
            raise ValueError("AlpacaBroker requires ALPACA_API_KEY and ALPACA_SECRET_KEY")
        self._base_url = base_url
        self._data_url = data_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=30.0)
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }

    # --- REST plumbing -------------------------------------------------------

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None, host: str | None = None) -> Any:
        resp = self._http.request(
            method, f"{host or self._base_url}{path}", headers=self._headers,
            json=json_body, params=params,
        )
        if resp.status_code >= 400:
            raise AlpacaError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:300]}")
        if not resp.content:
            return None
        return resp.json()

    # --- order path -----------------------------------------------------------

    def _latest_price(self, symbol: str) -> float:
        """Mark for the notional ceiling: latest trade from the market-data host
        (same key pair authenticates both hosts)."""
        try:
            data = self._request(
                "GET", f"/v2/stocks/{symbol}/trades/latest", host=self._data_url
            )
            if data and "trade" in data:
                return _as_float(data["trade"].get("p"))
        except AlpacaError:
            pass
        return 0.0

    def submit_order(self, request: OrderRequest, as_of: datetime) -> OrderResult:
        mark = self._latest_price(request.symbol)
        if mark <= 0 and request.limit_price:
            mark = float(request.limit_price)
        if mark <= 0:
            return self._rejected(request, f"no price available for {request.symbol}")

        rejected = self.check_rails(request, mark)
        if rejected is not None:
            return rejected

        if self.manual_approval:
            return self.queue_for_approval(request, mark, as_of)

        return self.place_order(request, client_order_id=self.mint_order_ref(request))

    def place_order(
        self, request: OrderRequest, *, client_order_id: str, extended_hours: bool = False
    ) -> OrderResult:
        """POST /v2/orders with a persisted idempotency key.

        ``extended_hours`` is valid only for limit DAY orders (Alpaca rule);
        used by ops tooling when proving fills outside regular hours.
        """
        body: dict[str, Any] = {
            "symbol": request.symbol,
            "qty": str(request.quantity),
            "side": request.side.value,
            "type": request.order_type.value,
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        if extended_hours:
            body["extended_hours"] = True
        if request.limit_price is not None:
            body["limit_price"] = str(request.limit_price)
        self._store.update_order_ref(client_order_id, state="submitted")
        try:
            order = self._request("POST", "/v2/orders", json_body=body)
        except Exception:
            self._store.update_order_ref(client_order_id, state="failed")
            raise
        venue_order_id = str(order.get("id", ""))
        self._store.update_order_ref(client_order_id, state="acked", venue_order_id=venue_order_id)
        logger.info("alpaca placed order %s (client_order_id=%s, status=%s)",
                    venue_order_id, client_order_id, order.get("status"))
        return OrderResult(
            order_id=venue_order_id,
            status=OrderStatus.PENDING,
            request=request,
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/orders/{order_id}")

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/v2/orders/{order_id}")

    # --- portfolio / reconciliation ------------------------------------------

    def get_portfolio(self, as_of: datetime) -> PortfolioSnapshot:
        account = self._request("GET", "/v2/account")
        positions = self._venue_positions()
        return PortfolioSnapshot(
            cash=_as_float(account.get("cash")),
            positions=positions,
            total_equity=_as_float(account.get("equity")),
            as_of=as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc),
        )

    def _venue_positions(self) -> list[Position]:
        rows = self._request("GET", "/v2/positions") or []
        out: list[Position] = []
        for r in rows:
            qty = _as_float(r.get("qty"))
            if qty == 0:
                continue
            avg = _as_float(r.get("avg_entry_price"))
            mv = _as_float(r.get("market_value"), default=qty * avg)
            out.append(
                Position(
                    symbol=str(r.get("symbol", "")),
                    quantity=qty,
                    avg_cost=avg,
                    market_value=mv,
                    unrealized_pnl=_as_float(r.get("unrealized_pl"), default=mv - qty * avg),
                )
            )
        return out

    def sync_positions(self) -> PortfolioSnapshot:
        """Authoritative state from the venue — never from memory (0.1b)."""
        return self.get_portfolio(datetime.now(timezone.utc))

    # --- fills ----------------------------------------------------------------

    def get_fills(self) -> list[Fill]:
        rows = self._request(
            "GET", "/v2/orders", params={"status": "closed", "limit": 100}
        ) or []
        fills: list[Fill] = []
        for r in rows:
            if r.get("status") != "filled":
                continue
            qty = _as_float(r.get("filled_qty"))
            price = _as_float(r.get("filled_avg_price"))
            if qty <= 0 or price <= 0:
                continue
            filled_at_raw = r.get("filled_at") or r.get("updated_at")
            try:
                filled_at = datetime.fromisoformat(str(filled_at_raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                filled_at = datetime.now(timezone.utc)
            fills.append(
                Fill(
                    fill_id=str(r.get("id", "")),
                    order_id=str(r.get("id", "")),
                    symbol=str(r.get("symbol", "")),
                    side=Side(str(r.get("side", "buy"))),
                    quantity=qty,
                    price=price,
                    filled_at=filled_at,
                )
            )
        return fills

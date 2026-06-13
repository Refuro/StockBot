"""Ticket 5a acceptance — Alpaca PAPER full order lifecycle, live against the API.

Place tiny paper order -> poll fill -> position reconcile -> cancel path ->
flatten. Run from the repo root:

    python scripts/alpaca_lifecycle_demo.py

Everything goes through the real AlpacaBroker (rails included). The paper host
is enforced by the broker itself. Outside regular hours the entry/exit use
extended-hours marketable limit orders (Alpaca only fills extended-hours limit
DAY orders); the cancel leg uses a far-below-market limit that can never fill.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockbot.config import load_env  # noqa: E402
from stockbot.core.broker.alpaca import AlpacaBroker  # noqa: E402
from stockbot.core.state.store import StateStore  # noqa: E402
from stockbot.types import OrderRequest, OrderType, Side  # noqa: E402

SYMBOL = "F"
QTY = 3.0  # ~ $45 at $15 — under the $50 pilot ceiling


def wait_for(broker: AlpacaBroker, order_id: str, *, want: set[str], timeout_s: float = 90.0) -> dict:
    deadline = time.time() + timeout_s
    order = broker.get_order(order_id)
    while order["status"] not in want and time.time() < deadline:
        time.sleep(3.0)
        order = broker.get_order(order_id)
    return order


def main() -> int:
    env = load_env()
    store = StateStore("data/alpaca_demo.db")
    broker = AlpacaBroker(
        store=store, mode="live",
        api_key=env.alpaca_api_key, secret_key=env.alpaca_secret_key,
        base_url=env.alpaca_base_url,
        manual_approval=False,  # the demo IS the manual approval
    )
    now = datetime.now(timezone.utc)
    print(f"== account (paper host: {env.alpaca_base_url}) ==")
    snapshot = broker.get_portfolio(now)
    print(f"cash={snapshot.cash:.2f} equity={snapshot.total_equity:.2f} "
          f"positions={[(p.symbol, p.quantity) for p in snapshot.positions]}")

    mark = broker._latest_price(SYMBOL)  # noqa: SLF001 - demo introspection
    print(f"\n== 1. place: {QTY} {SYMBOL} marketable limit @ {mark * 1.02:.2f} (mark {mark}) ==")
    entry_req = OrderRequest(symbol=SYMBOL, side=Side.BUY, quantity=QTY,
                             order_type=OrderType.LIMIT, limit_price=round(mark * 1.02, 2),
                             bot_id="5a-demo")
    rails = broker.check_rails(entry_req, mark)
    if rails is not None:
        print(f"RAILS REJECTED: {rails.rejection_reason}")
        return 1
    entry = broker.place_order(entry_req, client_order_id=broker.mint_order_ref(entry_req),
                               extended_hours=True)
    print(f"venue order id: {entry.order_id}")

    print("\n== 2. poll fill ==")
    order = wait_for(broker, entry.order_id, want={"filled", "canceled", "rejected"})
    print(json.dumps({k: order.get(k) for k in
                      ("status", "filled_qty", "filled_avg_price", "filled_at",
                       "client_order_id", "extended_hours")}, indent=2))
    if order["status"] != "filled":
        print("entry did not fill (after-hours liquidity) — stopping before reconcile")
        return 1

    print("\n== 3. reconcile positions from the venue ==")
    snapshot = broker.sync_positions()
    print(f"cash={snapshot.cash:.2f} equity={snapshot.total_equity:.2f}")
    for p in snapshot.positions:
        print(f"  {p.symbol}: qty={p.quantity} avg={p.avg_cost} mv={p.market_value:.2f}")
    fills = broker.get_fills()
    print(f"venue fills seen: {[(f.symbol, f.side.value, f.quantity, f.price) for f in fills]}")

    print("\n== 4. cancel path: far-below-market limit, then cancel ==")
    cancel_req = OrderRequest(symbol=SYMBOL, side=Side.BUY, quantity=1,
                              order_type=OrderType.LIMIT, limit_price=round(mark * 0.5, 2),
                              bot_id="5a-demo")
    lowball = broker.place_order(cancel_req, client_order_id=broker.mint_order_ref(cancel_req))
    print(f"lowball order id: {lowball.order_id} "
          f"(status={broker.get_order(lowball.order_id)['status']})")
    broker.cancel_order(lowball.order_id)
    cancelled = wait_for(broker, lowball.order_id, want={"canceled"}, timeout_s=30)
    print(f"after cancel: status={cancelled['status']}")

    print("\n== 5. flatten (sell the demo shares back) ==")
    exit_req = OrderRequest(symbol=SYMBOL, side=Side.SELL, quantity=QTY,
                            order_type=OrderType.LIMIT, limit_price=round(mark * 0.98, 2),
                            bot_id="5a-demo")
    exit_order = broker.place_order(exit_req, client_order_id=broker.mint_order_ref(exit_req),
                                    extended_hours=True)
    final = wait_for(broker, exit_order.order_id, want={"filled", "canceled", "rejected"})
    print(f"exit status: {final['status']} filled_avg_price={final.get('filled_avg_price')}")
    snapshot = broker.sync_positions()
    print(f"final: cash={snapshot.cash:.2f} "
          f"positions={[(p.symbol, p.quantity) for p in snapshot.positions]}")
    print("\nlifecycle complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

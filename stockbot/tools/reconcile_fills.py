"""Shot 12 — on-demand live-fill reconciliation CLI.

Live orders fill at the next regular-session open, AFTER the 5 PM tick that
queued/placed them, and Robinhood reports the realized price later. This CLI
lets the operator pull those realized fills into our `fills` table on demand —
e.g. the morning after approving an order, once they see it filled in Robinhood:

    python -m stockbot.tools.reconcile_fills              # reconcile (live broker)
    python -m stockbot.tools.reconcile_fills --broker robinhood_mcp
    python -m stockbot.tools.reconcile_fills --db data/state.db

It is **read-only against the venue**: it only polls fills (`get_fills`) and
writes to our DB. It places no order and cancels nothing. It is idempotent and
lands the IDENTICAL rows the on-tick reconciliation would — running it twice (or
after the tick already ran) is a no-op for already-recorded fills.
"""

from __future__ import annotations

import argparse
import json
import logging

from stockbot.config import load_config, load_env
from stockbot.core.broker.reconcile import reconcile_live_fills
from stockbot.core.state.store import StateStore

logger = logging.getLogger(__name__)


def _build_live_broker(name: str, store: StateStore):
    """Construct the named LIVE broker (manual_approval irrelevant — we only
    poll fills, never place). Mode is pinned to 'live' so the rails construct."""
    env = load_env()
    cfg = load_config()
    if name in ("robinhood", "robinhood_mcp"):
        from stockbot.core.broker.robinhood_mcp import RobinhoodMCPBroker

        return RobinhoodMCPBroker(
            store=store,
            mode="live",
            account_number=cfg.live_brokers.robinhood_account_number,
            mcp_url=env.robinhood_mcp_url,
            max_order_notional_usd=cfg.live_brokers.max_order_notional_usd,
            manual_approval=cfg.live_brokers.manual_approval,
            kill_switch_path=cfg.live_brokers.kill_switch_path,
        )
    if name == "alpaca":
        from stockbot.core.broker.alpaca import AlpacaBroker

        return AlpacaBroker(
            store=store,
            mode="live",
            api_key=env.alpaca_api_key,
            secret_key=env.alpaca_secret_key,
            base_url=env.alpaca_base_url,
            max_order_notional_usd=cfg.live_brokers.max_order_notional_usd,
            manual_approval=cfg.live_brokers.manual_approval,
        )
    raise ValueError(f"unknown live broker: {name!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile live fills (shot 12)")
    parser.add_argument("--broker", default=None,
                        help="live broker name (default: config broker, else robinhood_mcp)")
    parser.add_argument("--db", default=None, help="state DB path (defaults to config)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    db_path = args.db or cfg.state.db_path
    store = StateStore(db_path)

    name = args.broker or cfg.broker_name
    if name not in ("robinhood", "robinhood_mcp", "alpaca"):
        # config selected the simulated broker (paper/backtest); default to the
        # pilot's live broker for an explicit reconcile run.
        name = "robinhood_mcp"

    broker = _build_live_broker(name, store)
    summary = reconcile_live_fills(store, broker, mode="live", broker_name=broker.broker_name)
    print(json.dumps(summary.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

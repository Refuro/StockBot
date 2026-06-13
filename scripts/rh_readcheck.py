"""Ticket 9b — LIVE read-path verification for the Robinhood agentic account.

Runs the broker's REAL read path against the live Robinhood MCP on the agentic
account ••••0804:

    account guard (get_accounts + agentic_allowed)  ->
    get_portfolio (buying_power / equity)            ->
    get_equity_positions (reconciliation source)

and prints a clean, human-readable result with the account number MASKED. This
is the verification shot 4 claimed but never actually exercised against the real
envelope — it is the live proof that the ticket-9a `data`-envelope fix works.

READ-ONLY BY CONSTRUCTION: this script calls ONLY read tools (get_accounts,
get_portfolio, get_equity_positions) via the broker's read methods. It NEVER
calls submit_order / place_equity_order / review_equity_order. It places NO
order and moves NO money.

    python scripts/rh_readcheck.py

Requires a valid token (run `python -m stockbot.tools.rh_auth login` once). If
anything is `data`-wrapped differently than expected, it surfaces the error
here (the whole point is to find shape surprises now, not on pilot day).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from stockbot.config import load_config
from stockbot.core.broker.robinhood_mcp import RobinhoodMCPBroker
from stockbot.core.state.store import StateStore


def _mask(account_number: str) -> str:
    tail = account_number[-4:] if len(account_number) >= 4 else account_number
    return f"••••{tail}"


def main() -> int:
    cfg = load_config()
    account = cfg.live_brokers.robinhood_account_number
    masked = _mask(account)

    # The broker constructs in mode="live" (the rails require it); manual_approval
    # is irrelevant here — we call no order path. The state DB is opened only
    # because the broker needs a store handle; no order rows are written.
    store = StateStore(cfg.state.db_path)
    broker = RobinhoodMCPBroker(
        store=store,
        mode="live",
        account_number=account,
        manual_approval=True,
        kill_switch_path=cfg.live_brokers.kill_switch_path,
    )

    print("=" * 60)
    print(f"Robinhood LIVE read-path check — account {masked}")
    print("(read-only: no order is placed, no money moves)")
    print("=" * 60)

    try:
        # get_portfolio runs the full read path: _verify_account (account guard
        # + agentic_allowed) -> get_portfolio -> _venue_positions.
        snapshot = broker.get_portfolio(datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001 - surface any live shape/auth surprise
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print(
            "\nIf this is a 'no Robinhood token' error, run "
            "`python -m stockbot.tools.rh_auth login` first.\n"
            "If it is a shape/parse error, the live envelope differs from the "
            "ticket-9a fix — capture it and update robinhood_mcp.py."
        )
        return 1

    print(f"\n  account:        {masked}  (agentic_allowed verified)")
    print(f"  buying_power:   ${snapshot.cash:,.2f}")
    print(f"  total_equity:   ${snapshot.total_equity:,.2f}")
    print(f"  positions:      {len(snapshot.positions)}")
    for pos in snapshot.positions:
        print(
            f"    - {pos.symbol:6s} qty={pos.quantity:g} "
            f"avg=${pos.avg_cost:,.2f} mkt=${pos.market_value:,.2f}"
        )
    print("\nREAD-PATH OK — the data-envelope fix works against the live API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

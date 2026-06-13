# Shot 12 Handoff — Live-fill reconciliation & slippage logging (GO-LIVE GATING)

End-to-end build brief, **one ticket, one capable agent** (real-money data pipeline — careful, idempotent, well-tested). Reviewer audits. **This gates the pilot:** measuring realized-vs-modeled slippage is the pilot's stated purpose, and right now live fills are never recorded.

## The gap (reviewer-diagnosed 2026-06-13)
Live orders fill **asynchronously** — the 5 PM tick places (after approval), but the order fills at the **next regular-session open**, and the realized price comes back from Robinhood *later*. Today:
- `StrategyContext.settle_pending_orders` only handles the **SimulatedBroker** (`if not isinstance(self.broker, SimulatedBroker): return []`), so live fills are never settled into our DB.
- `RobinhoodMCPBroker.get_fills()` **exists** (polls `get_equity_orders state=filled` → venue `average_price` + fill time) but **has no caller** in the run loop.
- Result: a live order is placed and its `broker_order_refs`/`venue_order_id` are stored, but the **actual fill price/time never lands in the `fills` table**, and realized slippage vs the bot's expected price is never computed. The pilot would trade blind to exactly the number it exists to learn.

Paper works fully (simulated fills recorded with modeled slippage; dashboard has the realized-vs-assumed panel) — this shot brings **live** up to the same standard.

## Required reading
1. `stockbot/core/broker/robinhood_mcp.py` — `get_fills()` (venue poll; `average_price`, `filled_quantity`, `last_transaction_at`), `broker_order_refs`, `venue_order_id`, `place_reviewed_order`.
2. `stockbot/core/strategy.py` — `submit_and_record` (stores the decision-time `mark_price` = the expected/reference price) and `settle_pending_orders` (the sim-only settle).
3. `stockbot/core/state/store.py` — the `fills` schema (`price`, `slippage_pct`), `broker_order_refs`, `pending_approval_orders`, `record_fill`, `record_order_ref`.
4. `stockbot/tools/approve_orders.py` — where the live order is actually placed (mints/uses `ref_id`, sets `venue_order_id`).
5. `stockbot/dashboard/data.py` `slippage_summary` + the dashboard slippage panel — must work for `mode="live"` once fills land.

## Scope — build this

### 1. Persist the expected/reference price per live order
The bot's expected price exists on the **decision row** (`mark_price`), but isn't cleanly linked to the eventual fill. **Carry the expected price onto the order record** so slippage is a clean per-order computation, not a fragile join: add an `expected_price` (the decision-time mark) to `broker_order_refs` (and `pending_approval_orders` so it survives the approval queue). Populate it where the order is queued/placed.

### 2. A live-fill reconciliation step (the core)
A reconciler that, for the live broker, pulls realized fills and records them — **idempotent, at-least-once**:
- Call `broker.get_fills()` (venue poll). For each venue fill, **match to our order** by `venue_order_id` (preferred) or `ref_id`.
- **Record into `fills`** via `record_fill` with: realized `price` = venue `average_price`, `filled_at` = venue fill time, `quantity` = `filled_quantity`, `thesis_id`/`bot_id` from the matched order, `mode="live"`.
- **Compute `slippage_pct` = (realized − expected) / expected, side-aware** (for a BUY, paying *above* expected is positive/cost; for a SELL, receiving *below* expected is positive/cost). Store it signed and document the sign convention.
- **Idempotency:** never double-record a fill — dedupe on `venue_order_id` (+ fill id if the venue gives one); running the reconciler twice is a no-op for already-recorded fills. Handle **partial fills** (record what filled; leave the order open for the rest) and **unmatched venue rows** (log + skip, never guess).
- Update the `broker_order_refs` state (e.g. `filled`) so we don't re-poll settled orders forever.

### 3. Wire it in + make it runnable on demand
- **On the live tick:** after placement/settle, run the reconciler for any outstanding placed orders (it'll catch the prior session's fills — a ≤1-day lag is fine; fills happen at the open, after the prior 5 PM tick).
- **A standalone CLI** `python -m stockbot.tools.reconcile_fills` so the operator can pull fills on demand (e.g. the morning after approving, once they see it filled in Robinhood). Idempotent with the tick path.
- Optional: a morning scheduled task (after the open) — but the on-tick + on-demand CLI is the requirement; the scheduled task is a nice-to-have.

### 4. Dashboard: confirm live slippage shows
`slippage_summary` and the slippage panel read the `fills` table — once live fills land they should appear. **Verify** the realized-vs-assumed panel renders for `mode="live"`, showing **expected vs realized price and signed slippage per fill**. Extend minimally if it only assumed paper. (Don't collide with the in-flight shot-10 dashboard work — keep changes additive and in `data.py`/the slippage panel only.)

## Hard rules
1. **Read-only against the venue** — this shot polls fills and writes to our DB; it **places no order** and cancels nothing. No rail weakened; the account guard still refuses ••••2954.
2. Don't disturb paper/backtest (the sim settle path), the ride/fade/frontier/shadow lanes, RiskEngine, PIT, or existing tests.
3. Idempotent + at-least-once: a crash or a double-run must never double-record a fill or fabricate one. Prefer recording a fill late over recording it wrong.
4. Be honest in a code comment about what the slippage conflates: (decision mark → next-open fill) includes BOTH the overnight gap and execution slippage. That's acceptable for the pilot; a later decomposition (and the backlog marketable-limit idea) can refine it.

## Acceptance
- `expected_price` persisted from decision → order ref → fill (or recoverable for a clean per-fill slippage).
- Reconciler (unit-tested with a stubbed MCP returning filled/partially-filled/unmatched orders): records realized price + time + **correct signed slippage** vs expected; **idempotent** (second run = no-op); partial fills handled; unmatched rows logged not guessed.
- On-tick reconciliation for live mode + the `reconcile_fills` CLI, both landing identical rows.
- Dashboard slippage panel renders for `mode="live"` with expected-vs-realized.
- Full suite green; no existing test modified.

## Completion report (`reports/shot12-report.md`)
Files+tests (before/after + tail); the slippage sign convention; the reconciler design + idempotency proof (double-run test); expected-price plumbing; the CLI; dashboard live-slippage evidence; deviations; open questions. Confirm zero orders placed by this shot.

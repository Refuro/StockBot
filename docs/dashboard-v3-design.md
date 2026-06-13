# Dashboard v3 — design doc (strategy-organized redesign)

*Supersedes the queued `handoff-shot14-dashboard-lanes-pnl.md` (its reusable
parts are absorbed into Phase 1 below; its flat-panel HTML polish is dropped —
this redesign replaces those panels). Read-only over the paper DB, stays in the
current stdlib HTTP server, zero new dependencies, no JS framework.*

## Why
The dashboard was built for 2 lanes; we now run 7 across 4 strategies and will
add more. The current panels are "all lanes, all at once," so growth = a taller
stack and lost focus. Fix: reorganize the *information architecture* around
**engine → strategy → lane**, with a scannable overview and per-strategy
drill-downs. Complexity should grow **sideways into drill-downs**, never
**downward into a taller stack**. This is an IA change, not a tech change — the
existing server already does multiple routes; we keep its zero-dependency,
read-only, can't-break-trading property.

## The taxonomy (the thing that makes it expandable)
Everything hangs off a registry of **engine → strategy → lanes**, enumerated
dynamically (never hardcoded `bot_id`s). Today:

| Engine | Strategy | Lanes (brain / control) | Trades? |
|---|---|---|---|
| Proportionality | Earnings ride | `proportionality` (ride) · `proportionality_mech` | long |
| Proportionality | Crash fade | `proportionality` (fade-v1) · `crash_fade_v2` · `crash_fade_mech` | long |
| Frontier | Propagation up | `frontier` (up) · `frontier_mech` (up) | long |
| Frontier | Propagation down | `frontier` (down) · `frontier_mech` (down) | predict-only (no short) |
| Lens | Flow Lens, Crumbs | — | non-trading |
| (ops) | Shadow rehearsal | `proportionality_shadow` | rehearsal, go-live area |

**Adding a strategy later = register it here → it gets a scoreboard card + a
drill-down page automatically.** That is the whole point.

## Information architecture

### Global — on every page
- **Ops/health strip** (top, always visible — the reactive part the owner
  likes): ran-today + status, last/next scheduled runs (Central time),
  AI-ensemble health, kill-switch state, LLM spend vs cap, auth status.

### Home `/` — strategy scoreboard
- Cards grouped under **engine section headers** (Proportionality / Frontier /
  Lens). One **card per strategy** (not per lane): brain-vs-coin standing,
  sample size, progress toward its gate/falsifier, a status pill
  (too-early / measuring / passing / failing). Click a card → its strategy page.
- Engine grouping is visual only (collapsible sections) — not an extra nav level.

### Strategy page `/strategy/<id>` — everything about ONE idea, together
- Its lanes (brain / v2 / coin) and their standings.
- Open positions for those lanes (incl. frontier-up's real holdings).
- Closed trades + realized $ P/L + exit reason (this strategy only).
- Its brain-vs-coin race (chart + summary) and calibration.
- Fade only: the AI debate drill-down + debates index (fold in the existing
  `/debates`).

### Global cross-cutting pages
- **Execution `/execution`** — all fills across strategies, expected-vs-realized
  price + signed slippage (the $500-pilot view; superset of per-strategy closed
  trades).
- **Ops/go-live `/ops`** — scheduled-job detail, run history, spend
  reconciliation, auth, the shadow-lane rehearsal status, kill-switch + go-live
  controls reference.
- **Lens `/lens`** — Flow Lens consensus/novelty + Crumbs odd-lot feed
  (non-trading; with the "ran & empty vs didn't run" wording fix).

## Data-layer contract (Phase 1 — the foundation)
Strategy-agnostic, dynamically enumerated, in `dashboard/data.py`. No hardcoded
`bot_id`s anywhere. At minimum:
- `engines()` / `strategies()` — the registry (engine, strategy id, label,
  member lanes, brain vs control, tradeable vs predict-only).
- `scoreboard()` — per-strategy summary (standing vs coin, n, gate metric +
  bar, status) for the home cards.
- `strategy_detail(strategy_id)` — lanes, open positions, calibration, race
  series, (fade) debate refs.
- `closed_trades(strategy_id=None)` — buy/sell fills paired FIFO into round
  trips with realized $ P/L, exit reason, hold days. `None` = global Execution
  page; an id = that strategy.
- `race(strategy_id)` — generalize the current 2-lane `race_view` to N lanes /
  per-engine pairs (ride 1v1, fade 3-way, frontier sign-accuracy).
Every function handles the empty/n=0 case explicitly (most buckets are near-empty
today and must read sensibly).

## Build plan — two phases, two tickets, reviewed each
- **Phase 1 — strategy-agnostic data layer + closed-trade P/L + Crumbs wording.**
  Pure `data.py` (queries) + tests; no presentation redesign. Absorbs shot 14's
  reusable substance (dynamic enumeration, closed-trade P/L, Crumbs fix); drops
  shot 14's flat-panel HTML polish. Independently valuable, low-risk, de-risks
  Phase 2 by fixing the contract first.
- **Phase 2 — the IA/presentation:** scoreboard home (engine-grouped), strategy
  pages, Execution / Ops / Lens pages, the global ops strip, all on Phase 1's
  data layer. Mid-tier agent; read-only; restart + verify at :8765.

## Constraints (unchanged from prior dashboard tickets)
Read-only over the DB; no new write paths; stdlib HTML only (no JS framework);
no non-dashboard module touched; existing dashboard tests stay green; sentence
case; Central time on all timestamps. Mobile is not a concern (local desktop).

## No time pressure
The current dashboard works and stays running. The shadow-week review (~Jun 22/23)
is done by the reviewer from the DB directly, not the dashboard — so this redesign
can take its time and land after the shadow week without blocking anything.

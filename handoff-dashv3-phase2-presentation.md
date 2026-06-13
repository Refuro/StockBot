# Dashboard v3 — Phase 2: the strategy-organized UI (on Phase 1's data layer)

End-to-end build brief, **one ticket, one mid-tier agent** (read-only HTML/CSS/routing — broad but not deep; one agent owns the look + nav for consistency). **Requires Phase 1 merged + green** (`handoff-dashv3-phase1-datalayer.md`). Reviewer audits. Build in tier order; a polished partial beats a sprawling whole — report where you stopped.

**Spec:** `docs/dashboard-v3-design.md` (the "Information architecture" section is authoritative). This rebuilds the dashboard's presentation around engine → strategy → lane, reading **only** Phase 1's data-layer functions.

## Required reading
1. `docs/dashboard-v3-design.md` — the IA (home / strategy page / global pages / ops strip).
2. Phase 1's `data.py` functions (`engines`/`strategies`/`scoreboard`/`strategy_detail`/`closed_trades`/`race` + `ops.crumbs_feed_status`) — the only data source; do not add new queries here, and do not reach around them to raw SQL. Notes from Phase 1: the scoreboard returns 4 cards (`earnings_ride`, `crash_fade`, `frontier_up`, `frontier_down`) under 2 engine sections + a Lens engine with no lanes; `shadow_rehearsal` is excluded from the scoreboard (it belongs on `/ops`). The fade card's `gate` dict now carries `net_pl` + `reversion_bar` — show *both* the 55% reversion bar and the net-return condition, not hit-rate alone. **`strategy_detail(unknown_id)` raises `KeyError`** — the `/strategy/<id>` route must catch invalid ids and render a friendly "unknown strategy" page, never a 500.
3. `stockbot/dashboard/app.py` — current routes/render helpers, the shot-10 facelift CSS, the shot-13 `/debates` + debate section, the Central-time helper. Reuse the CSS/helpers; restructure the page composition.

## Scope — tiered (do in order)

### Tier 1 — the core IA (required)
- **Global ops/health strip** on every page (port the existing reactive content: ran-today + status, last/next runs in Central time, AI-ensemble health, kill switch, spend, auth). This is the part the owner values most — keep it prominent.
- **Home `/` = strategy scoreboard** — cards grouped under **engine section headers** (Proportionality / Frontier / Lens), one card per strategy from `scoreboard()`: brain-vs-coin standing, n, gate progress, status pill. Each card links to its strategy page. (See the approved mockup in the design discussion for the card shape.)
- **Strategy page `/strategy/<id>`** — from `strategy_detail()` + `closed_trades(id)` + `race(id)`: the strategy's lanes & standings, open positions per lane, closed trades + realized $ P/L + exit reason, the brain-vs-coin race, calibration; **fade strategy** folds in the shot-13 debate drill-down + debates index.

### Tier 2 — global cross-cutting pages
- **`/execution`** — `closed_trades(None)` + slippage: all fills across strategies, expected-vs-realized price + signed slippage (the $500-pilot view).
- **`/ops`** — scheduled-job detail, run history, spend, auth, shadow-lane rehearsal status, kill-switch/go-live reference.
- **`/lens`** — Flow Lens consensus/novelty + Crumbs feed, using the Phase-1 Crumbs helper so it reads "last scan {time} — none found" vs "didn't run."

### Tier 3 — polish (if headroom)
- Collapsible engine sections on the home scoreboard; a consistent nav header linking the global pages; facelift consistency pass. Keep data density (owner likes it) — clarify, don't strip.

## Migration rule (important)
The home page becomes the scoreboard; the **old flat panels are replaced**, their content **re-homed** into strategy pages / global pages. **Nothing currently shown may be lost** — every datum on today's dashboard must have a home in the new IA (positions, theses, vetoes, race, calibration, crumbs, flow lens, ops). Audit the current dashboard and confirm coverage in the report.

**Retire the legacy data functions, don't leave two sources of truth.** Phase 1 left the old `gate_scoreboard()` and `race_view(gated_bot_id, mech_bot_id)` in `data.py` for mid-migration safety — both still carry hardcoded `bot_id`s. Once the new pages read `scoreboard()` / `race(strategy_id)`, **delete those two legacy functions** (and the shot-13 `gate_scoreboard` panel) so lane identity lives only in the Phase-1 registry. Confirm in the report that no hardcoded `bot_id` literals remain in `data.py`/`app.py` outside the registry.

## Hard rules
1. Read-only over the DB; **no new write paths**; stdlib HTML only (no JS framework, no build step); reads go through Phase-1 data functions (no new raw SQL in `app.py`).
2. No non-dashboard module touched. Central time everywhere; sentence case; the shot-10 facelift conventions.
3. **Test exception (stated explicitly):** this redesign changes the panels, so existing **dashboard presentation** tests (`test_4b2_dashboard_v2.py`, `test_shot6_4b3_dashboard.py`, `test_shot10/13/…`) **may be updated** to match the new IA — that's expected. You may NOT weaken or remove Phase-1 **data-layer** tests or any non-dashboard test. Add new presentation tests for the scoreboard, a strategy page, and each global page (empty + populated DB).
4. Restart the dashboard task; verify all routes 200 at `http://127.0.0.1:8765` (home, a `/strategy/<id>`, `/execution`, `/ops`, `/lens`).

## If it sprawls
Tier 1 (ops strip + scoreboard + strategy pages) is the must-ship core. If Tier 2 global pages threaten quality, ship Tier 1 clean and spin the global pages into a Phase 2b — say so in the report. Don't half-build everything.

## Report (`reports/dashv3-phase2-report.md`)
Files+tests; the route map; a text/HTML snippet of the scoreboard home + one strategy page; the migration-coverage audit (every old datum's new home); which tiers shipped; which dashboard tests were updated and why; dashboard restarted + routes verified; deviations.

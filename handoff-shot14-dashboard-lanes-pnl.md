# Shot 14 Handoff — Dashboard catches up to all 7 lanes + closed-trade P/L

End-to-end build brief, **one ticket, one mid-tier agent**. Read-only dashboard work only (same hard rules as 4b-3/shot-10/shot-13: additive, **no new write paths**, SQLite read-only, stdlib HTML, no JS frameworks). Reviewer audits. **Do NOT touch** strategy/broker/risk/schema code or any non-dashboard test. Build in priority order; a polished partial beats a sprawling whole — say where you stopped.

**Why:** the dashboard's Open-Positions and Race panels are **hardcoded to `proportionality` + `proportionality_mech`** (`app.py` `_panel_positions` loops `(paths.gated_bot_id, paths.mech_bot_id)`; `data.py` `race_view` uses `lanes={gated,mech}`). The system now runs **7 lanes**, so the owner sees a 2-lane view of a 7-lane race — e.g. `crash_fade_mech` currently holds ALIT/BAND but isn't shown anywhere. Plus the owner wants **realized $ P/L on closed trades** (e.g. the mech RH round trip = **−$314.66**), and the Crumbs panel says "no alert feed yet" when it actually **ran and found nothing**.

## The 7 lanes (ground truth from `paper_run.py`)
`proportionality` (ride + fade-v1, one engine), `proportionality_mech` (ride coin), `proportionality_shadow` (rehearsal), `crash_fade_mech` (fade coin), `crash_fade_v2` (fade-v2 brain), `frontier` (propagation), `frontier_mech` (propagation coin). **Frontier is a HYBRID, not pure prediction-only:** its **UP side** (anchor rises → filer predicted up) places real long BUYs (it has already opened MQ ~$3k), so frontier **does hold equity positions** and **must appear in Open Positions** when it does; its **DOWN side** (anchor falls → filer predicted down) is prediction-only because shorting isn't possible on the long-only cash account, so those are recorded/scored on sign-accuracy with no position. Don't special-case frontier out of Open Positions — the dynamic "lanes that hold positions" enumeration handles it correctly (frontier shows up iff it currently holds shares).

## Tier 1 — the owner's direct asks (do these first)

### 1a. Open Positions: enumerate ALL lanes that hold positions (de-hardcode)
Replace the hardcoded `(gated_bot_id, mech_bot_id)` loop with **dynamic enumeration** of paper lanes that actually hold positions — use `d.list_bot_lanes(db)` (already exists) filtered to `mode="paper"`, and show each lane's positions section. A lane with no current holdings shows nothing/"flat" — don't fabricate rows, but **don't special-case any lane out** (frontier's UP side holds real positions, e.g. the pending MQ buy, and must appear once filled). Keep the existing per-position columns (symbol, qty, mark, P&L $, distance-to-exit). Result: `crash_fade_mech`, `frontier` (when its up-side buy is held), and `crash_fade_v2` (when it enters) all appear alongside the two proportionality lanes.

### 1b. Closed-trades view with realized $ P/L (new panel)
A new "Closed trades" table, newest first, pairing buy/sell **fills** per `(bot_id, symbol)` into round trips (FIFO; both legs are in the `fills` table — no new data capture). Columns: lane, symbol, entry price, exit price, **exit reason** (from the exit decision `action`: profit-target / trailing_stop / hard_stop / time_stop), hold days, qty, and **realized $ P/L = (exit − entry) × qty** (already net of modeled slippage, since fill prices include it). Verify against the known RH case: `proportionality_mech` bought 25.1067 RH @ $159.38, sold @ $146.85 → **−$314.66**. Handle still-open positions (no sell leg) by simply excluding them (they're in Open Positions) and partial/multi-lot fills FIFO; if multi-lot gets fiddly, do the clean single-buy/single-sell case well and note the limitation.

### 1c. Crumbs panel: distinguish "ran & empty" from "didn't run"
The Crumbs scan runs daily at 8:30 and writes `data/crumbs_alerts.json` (`[]` when nothing found — normal; odd-lot tenders are rare). Replace "no alert feed yet" with the honest state using the file's mtime + the ops job's last-run: e.g. **"Last scan {time CT} — no odd-lot tenders found"** when it ran and the list is empty, vs a real "hasn't run" only when the job/file is genuinely absent/stale.

## Tier 2 — the multi-lane race (more involved)
Generalize the Race view from one hardcoded pair to **per-engine brain-vs-coin sections**, each its own small cumulative + summary block:
- **Ride:** `proportionality` (gated) vs `proportionality_mech` (coin) — the existing one.
- **Fade:** the `crash:fade` bucket (v1, inside `proportionality`) vs `crash_fade_v2` (v2) vs `crash_fade_mech` (coin) — a three-way.
- **Frontier:** `frontier` vs `frontier_mech` — the gate metric is **sign-accuracy** (show hit-rate/n vs the 55% bar). The UP side also trades long so it carries a small P&L/equity too; lead with sign-accuracy (that's the falsifier), P&L secondary.
Each with an honest "too early (n=…)" tag. Reuse the existing race plumbing; keep `gated/mech` as the default/first section so nothing regresses. (If shot 13's gate scoreboard already covers some of this, link/complement rather than duplicate — don't collide with it.)

## Tier 3 — clarity polish (if headroom)
- In the Thesis browser, render the `quality` cell as **"n/a — mechanical"** (mech/coin lanes), **"n/a — fade"** (fade theses use the fade rubric, not the 2c quality gate), or **"n/a — frontier"** instead of a blank, so an empty score reads as *expected*, not missing. Where a fade rubric score exists, show it labeled as such.
- Label the existing "quality-gate vetoes" panel clearly as **the 2c earnings quality gate (gated lane)**, and note the fade counter-pass veto is shown in the debate view, not here.

## Acceptance
- Open Positions enumerates all position-holding paper lanes (verify `crash_fade_mech`'s ALIT/BAND appear); flat/prediction-only lanes don't fabricate rows.
- Closed-trades panel shows realized $ P/L + exit reason; the RH round trip reads **−$314.66**; open positions excluded; empty-DB state clean.
- Crumbs panel distinguishes ran-and-empty from didn't-run.
- Tier 2/3 as headroom allows; report where you stopped.
- New tests (`tests/test_shot14_dashboard.py`): dynamic positions enumeration (multi-lane fixture incl. a fade lane), closed-trade P/L pairing (incl. the RH numbers + a still-open exclusion), Crumbs ran-vs-empty wording, all with empty + populated DBs. Existing dashboard tests green; dashboard restarted + verified at `http://127.0.0.1:8765`.

## Report (`reports/shot14-report.md`)
Files+tests; the de-hardcoded positions enumeration; the closed-trade P/L logic + the verified RH −$314.66; the Crumbs wording fix; Tier-2 race generalization (or where stopped); Tier-3 clarity; deviations.

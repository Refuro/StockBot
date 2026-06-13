# Dashboard v3 — Phase 1: strategy-agnostic data layer (+ closed-trade P/L, Crumbs fix)

End-to-end build brief, **one ticket, one capable-leaning-mid agent** (logic/aggregation-heavy and it's the foundation — correctness here compounds downstream). Read-only over the paper DB; **pure data layer — NOT presentation.** Reviewer audits. Bar set by `reports/shot1..13-report.md`.

**Spec:** `docs/dashboard-v3-design.md` (the "Data-layer contract" + "taxonomy" sections are authoritative). This ticket builds *only* the data layer that the Phase-2 redesign will render. Supersedes the data-layer substance of the retired `handoff-shot14-dashboard-lanes-pnl.md`.

## Why
The current `data.py` hardcodes `bot_id`s (`race_view` literally `lanes={gated,mech}`; the positions panel loops the two proportionality lanes). The system runs 7 lanes / 4 strategies and will grow. Phase 1 makes the data layer **enumerate engines/strategies/lanes dynamically** and adds the queries the new IA needs — so adding a strategy later requires no query changes.

## Required reading
1. `docs/dashboard-v3-design.md` — the taxonomy table + data-layer contract.
2. `stockbot/dashboard/data.py` — extend; reuse existing helpers (`list_bot_lanes`, `equity_curve`, `slippage_summary`, `race_view`, calibration reads).
3. `stockbot/harness/paper_run.py` — the lane/engine/strategy ground truth (`*_BOT_ID` constants; which lanes are brain vs control; frontier up/down; prediction-only vs tradeable).
4. `stockbot/bots/proportionality/ride.py` + `fade.py` — bucket names (`earnings:ride`, `crash:fade`, `crash:fade_v2`, `frontier:*`), `trade_type`s, exit `action`s.

## Scope — `data.py` (and `ops.py`/`glossary.py` only if needed). NO `app.py` redesign.

1. **The registry** — `engines()` / `strategies()`: a dynamically-built map of engine → strategy → member lanes, each lane tagged brain/control, tradeable/predict-only. Derive from the lanes actually present in the DB + the known bucket/bot_id conventions; **no hardcoded one-off `bot_id` literals scattered through queries** — centralize the mapping here. New strategy → appears automatically once its lane has rows.
2. **`scoreboard()`** — per-strategy summary for the home cards: brain-vs-coin standing, sample n, the strategy's gate metric + bar (ride = Brier vs 0.25 over k/10; fade = signals vs 40 + reversion rate; frontier = sign-accuracy vs 55%), and a status (too-early / measuring / passing / failing). Every field handles n=0.
3. **`strategy_detail(strategy_id)`** — that strategy's lanes + standings, open positions per lane, calibration, race series, and (fade) the debate decision refs. Reuse the shot-13 debate reconstruction.
4. **`closed_trades(strategy_id=None)`** — pair buy/sell **fills** per `(bot_id, symbol)` FIFO into round trips: entry price, exit price, **exit reason** (from the exit decision `action`: profit-target / trailing_stop / hard_stop / time_stop), hold days, qty, **realized $ P/L = (exit − entry) × qty** (already net of modeled slippage). `None` = all strategies (the global Execution page); an id filters to that strategy. **Verify: `proportionality_mech` RH = bought 25.1067 @ $159.38, sold @ $146.85 → −$314.66.** Exclude still-open positions (no sell leg); handle multi-lot FIFO, and if multi-lot gets fiddly do single-buy/single-sell cleanly and document the limit.
5. **`race(strategy_id)`** — generalize `race_view` from the hardcoded 2-lane pair to N lanes / per-engine shape: ride = gated vs mech; fade = v1 vs v2 vs coin (3-way); frontier = sign-accuracy (hit-rate/n vs 55%, the up side also has a small P&L). Keep the old `race_view(gated,mech)` working (or wrap it) so nothing regresses mid-migration.
6. **Crumbs ran-vs-empty** — a helper (in `data.py`/`ops.py`) that distinguishes "ran & found nothing" (file exists, `[]`, recent mtime) from "didn't run" (absent/stale), for Phase 2 to render. (The scan runs 8:30 daily, writes `data/crumbs_alerts.json`.)

## Hard rules
1. **Read-only**, no new write paths, no non-dashboard module touched, stdlib only.
2. **No hardcoded `bot_id` literals in query bodies** — all lane identity flows from the registry. This is the whole point.
3. Every function returns sensible output on an **empty/near-empty DB** (most buckets are n=0 today).
4. Don't redesign `app.py` here — if a tiny `app.py` touch is unavoidable to keep the page rendering, keep it minimal and additive; the real presentation work is Phase 2.

## Acceptance
- Registry enumerates the 4 current strategies / 7 lanes with correct brain/control + tradeable/predict-only tags, with **zero hardcoded bot_id literals in query logic**.
- `closed_trades()` reproduces RH = **−$314.66** and excludes open positions; `scoreboard()`/`strategy_detail()`/`race()` return correct shapes on both empty and populated DBs.
- New tests (`tests/test_dashv3_datalayer.py`): registry enumeration (multi-lane fixture incl. fade + frontier), FIFO P/L (RH + an open-exclusion + a multi-lot case), race generalization (3-way fade), scoreboard empty + populated, Crumbs ran-vs-empty. **All existing tests stay green** (Phase 1 doesn't change presentation, so existing dashboard tests must not break).

## Report (`reports/dashv3-phase1-report.md`)
Files+tests; the registry design (how lane identity is derived, not hardcoded); the data-layer functions + signatures; the RH −$314.66 verification; empty-state handling; deviations; open questions.

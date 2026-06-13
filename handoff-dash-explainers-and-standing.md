# Dashboard — per-bucket standing fix + inline explainers (fade table, Flow Lens/Crumbs)

End-to-end build brief, **one ticket, one mid-tier agent**. Read-only dashboard work (additive, no new write paths, stdlib HTML, no JS framework, reads go through the registry/data-layer — no raw `bot_id` SQL). Reviewer audits. The `/ops` HTML-escape bug from the same review is **already fixed by the reviewer** — not in scope here.

## Part 1 — per-strategy "brain vs coin" standing must be per-bucket (correctness — priority)
**The bug (owner-spotted):** the `proportionality` lane runs BOTH strategies (ride + fade) from one account — intentional ("one engine, two trade types," mirrors live). But `_brain_vs_control_standing` (in `data.py`) computes the brain's standing from that lane's **total equity** (`equity_curve(brain.bot_id)`). So:
- the **earnings_ride** card compares a ride+fade book against a ride-only coin, and
- the **crash_fade** card compares the *same* ride+fade book against a fade-only coin.
Both cards show the same commingled proportionality equity as "brain" → the per-strategy comparison is contaminated.

**The fix (do NOT split the lane — that breaks the live-mirroring design):** compute each strategy's standing from that strategy's **own bucket's realized P/L**, which IS cleanly attributable. Use `closed_trades(db, strategy_id=...)` (already filters fills to the strategy's bucket via `_fill_matches`) and sum `realized_pl` **per lane within that strategy**: brain = the brain lane's bucket P/L, coin = the coin lane's P/L. So `earnings_ride` standing uses proportionality's `earnings:ride` trades vs `proportionality_mech`; `crash_fade` uses proportionality's `crash:fade` trades vs `crash_fade_mech`. The calibration/hit-rate metrics are already per-bucket and correct — leave them; this is only the equity-standing.
- Label it **"realized P/L"** (closed trades only). If including open-position unrealized is easy via thesis→bucket attribution, do it and label "realized + open"; otherwise realized-only is the honest v1 — state which.
- Result: the two cards now show **different, correctly-attributed** standings.

## Part 2 — inline explainers (clarity)
Add `<details>`/"what am I looking at?" blocks (same pattern as the existing panels), copy in plain language:

**Fade decisions table** (on the crash_fade strategy page): explain the actions — `skipped_fade_gate` = "looked at a ≥10% crash and chose not to fade it — the common, healthy outcome"; `fade_buy` = "actually faded"; `fade_vetoed` = "the devil's-advocate AI killed it"; `ensemble p` = "the two AIs' blended probability the stock bounces within 10 days; needs ≥0.55 to enter" (and `—` = "skipped before a probability was computed — no source filing / triaged out"); `outcome: open` = "this is a decision log, not a position; 'open' = the 10-day window hasn't elapsed to score it yet."

**Flow Lens & Crumbs** (`/lens`): **Crumbs** = "a scanner for odd-lot tender offers (a niche corporate-action signal); it doesn't trade — 'none found' is normal, they're rare." **Flow Lens** = "a sensor, not a trader: it asks several AI personas what tickers people are paying attention to and logs the overlap. consensus = how strongly the personas agreed; novel? = newly appearing; models = which personas; prompts = how many of the day's prompts mentioned it. It's accumulating attention data for the F2 study (mid-July) — there's nothing to act on yet, by design."

## Part 3 — ops health must reconcile the scheduled-log status with the DB (stop the self-contradiction)
**The bug (owner-spotted):** the `/ops` panel derives "today's paper tick" status purely from the scheduled-task log (`logs/paper-tick-<day>.log` exit code). When a tick is killed mid-run (e.g. a wifi drop) and then **recovered by a manual re-run** (which writes a different log + a completed run in the DB), the panel shows a red "Paper tick exit -1 / FAILED" *right next to* "last tick … (completed)" — contradicting itself. The data is fine; the panel is reading the wrong source.
**The fix:** for the paper-tick health specifically, **cross-reference the DB** — if `last_run_info`/the runs+snapshots show a paper tick **completed today**, treat today's paper tick as **ok** (optionally annotate "scheduled run failed — recovered by re-run"), overriding a stale failed exit code in the scheduled log. The DB (a completed run + today's snapshots) is the source of truth for "did we get a tick today," not the scheduler's last exit code. Keep the other three jobs (ingestion/crumbs/flow-lens) log-based as-is. The app already calls `d.last_run_info` for the "last tick" line — reuse it.

## Hard rules
1. Read-only; additive; no new write paths; stdlib HTML; sentence case; Central time.
2. Don't split the proportionality lane or change any strategy/trading code — Part 1 is a dashboard *scoring* fix only.
3. No raw `bot_id` literals in queries — go through the registry / `closed_trades(strategy_id=...)`.
4. Existing dashboard + data-layer tests stay green; the route-level smoke tests (`tests/test_dashv3_routes.py`) stay green.

## Acceptance
- `earnings_ride` and `crash_fade` scoreboard cards (and strategy pages) show **distinct** brain standings derived from each strategy's own bucket P/L — verified by a test with a fixture where proportionality has both a winning ride trade and a losing fade trade (the two standings must differ and each match its bucket).
- Explainers render on the crash_fade page and `/lens`.
- New tests in the dashboard suite; full suite green; dashboard restarted + `/`, `/strategy/crash_fade`, `/lens` verified at :8765.

## Report (`reports/dash-explainers-standing-report.md`)
Files+tests; the per-bucket standing logic + the fixture proving ride vs fade standings differ; the explainer copy; dashboard verified; deviations.

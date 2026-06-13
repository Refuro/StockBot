# Shot 10 (small) — Dashboard clarity + facelift (user-driven)

End-to-end build brief, **one ticket, one cheap-to-mid agent**. Read-only dashboard work; same hard rules as 4b-3 (additive, no new write paths, stdlib HTML only, no JS frameworks). Reviewer audits. **Do NOT touch** strategy/broker/lane code, the DB schema, or any existing test.

**Why:** the owner (basic-investing background, not a quant) reads the dashboard daily and several things are unclear or mis-presented. The dashboard is **information-dense by design and the owner likes that** — DO NOT simplify away data. Make it *clearer*, better presented, and prettier, while keeping (or increasing) what's on screen.

Files: `stockbot/dashboard/app.py`, `data.py`, `ops.py`, `glossary.py`. New test file `tests/test_shot10_dashboard.py`. Keep the existing 15 dashboard tests green.

## Required fixes (the owner hit each of these)

1. **Timezone → America/Chicago (Central, the owner's timezone).** Every timestamp currently renders in UTC (e.g. `2026-06-12T03:00`). Convert all displayed times to **US Central with DST** (CDT/CST) and label them (e.g. `2026-06-11 10:00 PM CDT`). Use a single helper; keep stored data UTC, convert only at render. Put the timezone in one config constant so it's changeable.

2. **Explain the flags — they're currently bare chips with thin tooltips.** Add a small **always-visible legend** under the Thesis-browser and quality-veto tables (not just hover tooltips) mapping every flag to plain English. Cover at minimum: `ensemble_unavailable` / `cross_model_failed` ("the GPT cross-check failed this run — graded by Claude only and capped at 0.60 confidence; usually means the OpenAI account was out of funds"), `ensemble_disagreement` ("the two AIs differed >0.15 — we kept the lower number"), `counter_pass_veto`, `no_filing_text` / `no_source_doc` ("couldn't fetch the filing/source doc, so it wasn't scored"), `triaged_out`, `hard_skip_binary_resolution`, `forced_flow`, `degraded`. Source the text from `glossary.py` (one source of truth) so hover tooltips and the legend never drift.

3. **Explain & de-confuse the "surprise" column.** `surprise = (actual EPS − consensus EPS) / |consensus|`, shown as a %. A **+100% surprise means actual was double the estimate** — but for small-cap names with a tiny consensus (a few cents), a small dollar beat balloons into a huge percent. So:
   - Add a "What is this?" note explaining the formula in plain words.
   - **Show the underlying numbers** where available: `actual EPS` vs `consensus EPS` (the data exists — `actual_eps`/`consensus_eps` in the events; surface them as columns or a tooltip on the surprise cell). The raw 8¢-vs-4¢ is far more legible than "+100%".
   - **Flag small-denominator surprises** (e.g. |consensus| < $0.10) with a subtle marker + tooltip ("tiny estimate — the % overstates the real beat"), so the owner isn't misled by a giant percentage.

4. **The "stated p" / "quality" columns need a one-line explainer** in the panel's "What am I looking at?" block: `stated p` = the model's probability the thesis works; `quality` = the 2c quality-gate score; the mechanical lane shows a flat 0.55 and no quality (that's expected, not a bug — say so).

## Facelift (the owner explicitly invited it — keep the density)
Within the same ticket, a tasteful visual pass: consistent spacing/borders/typography, better column alignment and number formatting (right-aligned monospaced numbers, signed/colored returns), clearer panel headers, subtle zebra striping or grouping, and good empty-state text. Keep every existing panel and column — this is polish, not reduction. Mobile/narrow is not a concern (local desktop view).

## Operational note to surface (not a fix, just display it)
Because the 6/12 ticks show `cross_model_failed`, add a small **"AI ensemble health" indicator** to the ops strip: if the most recent gated-lane theses are flagged `ensemble_unavailable`, show a soft warning ("GPT cross-check was unavailable on the last run — check OpenAI funding"). Read-only, derived from the flags already in the DB.

## Acceptance
- All displayed times are Central with a TZ label; one render helper; stored data still UTC.
- Flag legend visible (not hover-only) and sourced from `glossary.py`; every flag in the current DB is explained.
- Surprise column shows actual-vs-consensus (or tooltip) and marks small-denominator cases.
- Average-joe test still passes (the owner can answer is-it-working / who's-winning / real-money-at-risk), now without confusion on flags or surprise.
- Existing 15 dashboard tests green; new tests cover the TZ helper (incl. a DST boundary) and the surprise-detail/flag-legend rendering with empty + populated DBs.
- Restart the dashboard task; confirm at `http://127.0.0.1:8765`. Capture a before/after text or HTML snippet in the report.

## Report (`reports/shot10-report.md`)
Files+tests (before/after + tail); the TZ helper; the flag legend (list every flag + its text); the surprise-detail change; the facelift summary; the ensemble-health indicator; dashboard restarted + verified; deviations.

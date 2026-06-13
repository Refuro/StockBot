# Shot 6 — Agent A brief: dashboard readability layer (Ticket 4b-3)

**You are one of two parallel build agents on shot 6. You own ONLY ticket 4b-3.** The other agent owns the guard/graph work — you will not touch the same files, so work freely in parallel. Cheapest capable model is fine for this; it's careful plumbing, not algorithms.

## Ground rules (hard)
- **Additive only.** Do not remove, reorder, or change any existing panel, query, or number. You are adding an explanatory layer on top.
- **Read-only dashboard stays read-only.** No new write paths. SQLite stays opened read-only. No new scheduled tasks, no DB schema changes.
- **Stdlib HTML only.** No JS frameworks, no build step, no new dependencies. `<details>`/`<summary>`, `title=` tooltips, and server-rendered strings are the whole toolkit.
- **Do not touch** anything under `stockbot/bots/`, `stockbot/core/`, `stockbot/harness/`, `core/broker/`, `paper_run`, the fade pipeline, or any test other than ones you add. The live race must not be disturbed.
- Never modify existing tests. The suite is at 254 passed and must stay green.

## Files you'll work in
- `stockbot/dashboard/app.py` (~682 lines) — the HTTP handler + HTML rendering. Your main file.
- `stockbot/dashboard/data.py` — read-only queries. You may ADD pure read-only query helpers here if a verdict line needs a number not already fetched; do not change existing ones.
- `stockbot/dashboard/ops.py` — ops-health helpers (last tick, scheduled-job status). Reuse, don't change.
- New: `stockbot/dashboard/glossary.py` (or a dict in app.py) for the term/tooltip copy — ONE source of truth.
- New: `tests/test_shot6_4b3_dashboard.py` — your tests.

Read `app.py` top-to-bottom first; it documents its seven panels in the module docstring. Reuse its existing formatting helpers (`_fmt_pct`, `_fmt_usd`, `_esc`, etc.) and CSS classes (`.chip`, `.flag`, `.muted`, `.ok/.no/.warn`).

## What to build (6 parts)

1. **Top-of-page summary strip** (above panel 1). 3–4 plain sentences a non-trader reads in 10 seconds:
   - *What is this?* Four bots paper-trading $100k each of **fake** money on real market data, to find out which approach is real before any real money is used.
   - *Is real money at risk?* No. State the gates that must pass first (shadow week clean → calibrated bucket → $500 pilot → only then scaling).
   - *Who's winning?* Each lane's return since inception, days running, trade count — pulled from the same equity-curve query the race panel uses.
   - *Did it run today?* Last tick time + next expected tick, **holiday-aware** via `stockbot.harness.trading_calendar.is_us_trading_day` (import it; don't reimplement). If today is a non-trading day, say so ("market closed — next tick Mon").

2. **Per-panel "What am I looking at?" explainers.** A collapsed `<details><summary>What am I looking at?</summary>…</details>` at the top of each of the 7 panels. Plain English. Examples to match in tone:
   - *Race view:* "The whole experiment: the AI-gated lane vs an identical lane that just flips a coin (constant 55%). If the AI can't beat the coin, the AI layer is earning nothing and we'd cut it."
   - *Calibration:* "When a bot says 70%, does it happen about 70% of the time? The Brier score measures that: 0 = perfect, 0.25 = no better than a coin. A bucket needs Brier ≤ 0.25 over 10+ resolved predictions before it can EVER touch real money."
   - *Open positions:* explain each exit rule in plain words (−8% hard stop, trailing stop, time-stop) and what "distance to stop" means.

3. **Computed plain-English verdict lines** under the race and calibration panels. **Deterministic templates only** — fill blanks from the existing queries, never call an LLM, never randomize. Cover the zero/empty cases explicitly (June is sparse; expect 0 fades and few rides). Examples:
   - `"After {n} resolved trades, the AI lane is {ahead of/behind/tied with} the coin-flip lane by {x}% — {too early to mean anything until ~40 trades / a meaningful sample now}."`
   - `"Bucket {bucket}: {k} of 10 required predictions resolved, Brier {b} — {not yet eligible / eligible} for real-money consideration."`
   - n=0 case: "No trades have resolved yet — nothing to compare. The race just started."

4. **Hover tooltips** (`title=`) on **every table column header** and **every flag chip**. One source-of-truth dict (the glossary module) maps `flag_name → plain explanation`, e.g. `ensemble_disagreement` → "the two AIs disagreed by more than 0.15; we kept the LOWER probability to be safe"; `counter_pass_veto` → "the devil's-advocate pass talked the primary AI out of the trade"; `no_source_doc` → "no SEC filing explained the crash, so we didn't grade it." Render existing chips/headers through this dict so wording never drifts.

5. **`/glossary` page** — a new route in the HTTP handler, linked from the header and (optionally) from jargon terms. Short, honest, no marketing voice. Terms: lane, thesis, bucket, ride vs fade, mech twin, shadow lane, ensemble, counter-pass / veto, Brier score, calibration gate, time-stop / trailing / hard stop, slippage, PIT, kill switch, paper vs pilot vs live.

6. **Friendlier formatting:** relative times ("2 h ago") next to absolute timestamps; a "(fake $)" annotation on paper equity figures; a phase-journey strip near the top — `backtest ✅ → paper ▶ → $500 pilot ⏳ → scaled ⬜` — with the gate each arrow represents shown on hover.

## Acceptance
- **Average-joe test:** from the dashboard alone, a non-trader can answer: Is it working? Is real money at risk? Who's winning — brain or coin? What has to happen before real money? Put these answers within reach of the summary strip + explainers.
- Existing panels/numbers byte-for-byte unchanged (additive only).
- `tests/test_shot6_4b3_dashboard.py`: unit-test every verdict template incl. **n=0 / empty / zero-fade** cases (deterministic in, deterministic out); assert `/glossary` returns 200 and contains the key terms; assert the summary strip renders with an empty DB without throwing.
- Restart the dashboard task and confirm it serves at `http://127.0.0.1:8765`. Capture the rendered summary-strip + one explainer as an HTML/text dump for the report.
- Full suite green (run `.\.venv\Scripts\python.exe -m pytest tests/ -q`).

## Report
Append a `### 4b-3` section to `reports/shot6-report.md` (the other agent owns the rest of that file — only add your section, don't rewrite theirs): files touched, test count before/after, the average-joe answers with where each lives on the page, the captured HTML/text snippet, and any copy you were unsure about (so the reviewer can adjust wording).

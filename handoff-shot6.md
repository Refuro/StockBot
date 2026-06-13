# Shot 6 Handoff — Guard round 3 + graph re-filter + 50-edge precision sample (seed 3004)

End-to-end build agent brief, **shot 6 only**. Reviewer audits at the end. Bar set by `reports/shot1..5-report.md`.

**Why this shot exists:** the seed-3003 precision sample graded **32/50 = 64%** vs the ≥90% bar (`reports/3b-grading-3.md`). 3c/3d trading code stays FROZEN until a fresh sample passes. The graph is salvageable — every edge retains its source quote — so this is a guard-and-refilter pass, not a rebuild.

## Required reading
1. [reports/3b-grading-3.md](reports/3b-grading-3.md) — **the failure taxonomy IS the spec.** Read every one of the 18 failures and the reasons.
2. [reports/3b-precision-sample-3.md](reports/3b-precision-sample-3.md) — the graded quotes (your offline fixture set).
3. [implementation-plan.md](implementation-plan.md) — shot-5 review block + the 5-R1 fix description, conventions.
4. The existing graph builder + round-2 guards (3a-F/3a-G code) — extend, don't fork.

## Environment facts
- Suite **254 passed** — stays green; never modify existing tests (includes the reviewer's `tests/test_shot5_review_weekend.py`).
- Paper race + shadow week + fade lanes are LIVE on the 17:00 tick (trading days only since 5-R1; **Fri Jun 19 is a market holiday** — the tick correctly skips it). **Do not disturb any of it**: no changes to lanes, `paper_run`, exits, RiskEngine, PIT, the feed cursor, or the fade pipeline.
- Brokers exist but are NOT in scope — zero real orders, zero changes under `core/broker/`.
- Graph lives in `data/state.db` (373 edges, 179 named); quotes retained on every edge; doc text re-fetchable via the cached EDGAR plumbing at ~zero cost.

## Scope — build ONLY this, in this order

### Ticket R1 (riders) — DONE/DEFERRED by the reviewer 2026-06-12, do not redo
- **`data/fade_demo/`**: already deleted; log preserved at `logs/fade-demo-tick-2026-06-12.log`. ✅
- **gpt-5.5 price row**: DEFERRED — blocked on the user's settled June OpenAI reading (available after July 1). Leave the padded `(7.00, 40.00)` row as-is; do not touch `router.py`. ⏳
- **Four-lane tick confirmation**: NOT a build-time task. The first fade-enabled tick is Mon **Jun 15** 17:00 at the earliest (today is Jun 12; 13/14 weekend, skipped by 5-R1). The reviewer verifies it at shadow-week review — builders touch nothing here. ⏳

### Ticket 4b-3 — dashboard readability layer (user-requested; additive, read-only)
The dashboard is information-complete but written for the builder, not the owner. Add a plain-English layer ON TOP of the existing panels — remove nothing, dumb down nothing, and keep the zero-new-write-paths rule (stdlib HTML only; no JS frameworks; `<details>`/`<summary>` + `title` tooltips are enough).

1. **Top-of-page summary strip** answering, in 3–4 sentences a non-trader can read: What is this? (four bots paper-trading $100k each of FAKE money on real market data); Is real money at risk? (no — and say what would have to happen before it ever is); Who's winning? (each lane's return since start, days running, trade count); Did it run today? (last tick + next expected tick, holiday-aware via `trading_calendar`).
2. **Per-panel "What am I looking at?" explainers** — a collapsed `<details>` block atop each of the seven panels, e.g.: Race view = "the whole experiment: the AI-gated lane vs an identical lane that flips a coin. If the AI doesn't beat the coin, the AI layer earns nothing"; Calibration = "when the bot says 70%, does it happen ~70% of the time? Brier score: 0 = psychic, 0.25 = coin flip — below 0.25 on 10+ resolved predictions is required before a strategy can EVER touch real money"; Open positions = what each exit rule means in plain words and what "distance to stop" is telling you.
3. **Computed plain-English verdict lines** under the race and calibration panels — deterministic TEMPLATE sentences filled from the same queries (never LLM-generated; no spend, no nondeterminism). Examples: "After {n} resolved trades, the AI lane is {ahead/behind} the coin-flip lane by {x}% — {too early to mean anything until ~40 trades / now a meaningful sample}." / "Bucket {earnings:ride}: {k} of 10 required predictions resolved, Brier {b} — {not yet eligible / eligible} for real-money consideration." Handle the empty/zero cases explicitly (June is sparse; the dashboard must read sensibly with 0 fades and few rides).
4. **Hover tooltips (`title=`) on every table column header and every flag chip** — e.g. `ensemble_disagreement` → "the two AIs disagreed by >0.15; we kept the LOWER probability", `counter_pass_veto` → "the devil's-advocate pass talked the primary AI out of it". One source-of-truth dict in code so the wording stays consistent.
5. **A `/glossary` page** (linked from the header and from jargon terms): lane, thesis, bucket, ride vs fade, mech twin, shadow lane, ensemble, counter-pass/veto, Brier score, calibration gate, time-stop/trailing stop, slippage, PIT, kill switch, paper vs pilot vs live. Short, honest, no marketing voice.
6. **Friendlier formatting**: relative times ("2 h ago") alongside timestamps; "(fake $)" annotation on paper equity numbers; the phase journey strip "backtest ✅ → paper ▶ → $500 pilot ⏳ → scaled ⬜" with the gate each arrow represents.

Acceptance: an average-joe test — someone with no trading background can answer from the dashboard alone: Is it working? Is real money at risk? Who's winning, brain or coin? What has to happen before real money? Plus: existing panels/data unchanged (additive only), templates unit-tested incl. zero-data cases, dashboard task restarted and verified at :8765.

### Ticket 3a-H — guard round 3 (the whole point of the shot)
Implement the following guards as **pre-registered code constants/patterns committed BEFORE the re-filter is run** (hard rule: if you tune a pattern after seeing re-filter results on non-fixture edges, stop and write it down). Extend the round-2 guard module; each guard logs its name on every kill.

- **G1 — internal-entity counterparty**: the counterparty must be external. Kill own segments/plants/systems/divisions/verticals (taxonomy edges #4 BMD segment, #25 Middlesex System, #27 APT plant, #36 client vertical). Signals: "our/the Company's <X>", "<X> segment", "client vertical", facility words.
- **G2 — non-entity counterparty**: kill geographies (#26 "Italy"), legal instruments (#15 "Consent Decree"), and vague government collectives (#30 "U.S.", #46 "federal and state governments"). `regulatory_precedent` counterparties must be a NAMED agency/regulator (EPA, DOJ, FTC, USEPA, HHS-OIG, state AG by name...).
- **G3 — anonymous-in-named classifier**: "Customer \"A\"", "Initial Customer", "US-based IT OEM" and other descriptor-only labels (#7, #37, #43) are ANONYMOUS, never named. Quoted single letters, ordinal labels, and nationality/industry descriptors without a proper name all reclassify.
- **G4 — relationship currency**: terminated/ended/historical relationships are killed (#6 "did not renew... ended June 14, 2025", #50 "has historically been... we have moved"). Signals: "did not renew", "terminated", "expired", "ended", "historically been", "former", "no longer", "we have moved/transitioned away".
- **G5 — magnitude denominator**: a magnitude is only valid against a company-level denominator (net sales / total revenue / consolidated revenues / gross revenues). Segment- or subsidiary-denominated figures (#8 Garden segment, #40 Affiliate Marketing segment, #4, #7) **null the magnitude and set `denominator_scope="segment"`**; the edge survives only if the quote still asserts the typed relationship without the number. (Pre-registered by the reviewer: null-magnitude edges are graded on relationship + resolution only, same as sole_supplier edges today.)
- **G6 — latest-year selection**: when a quote carries a multi-year series, the magnitude must be the LATEST year's figure (#28: claimed 39% = FY2024 when FY2025 = 14% sat in the same sentence).
- **G7 — sole_supplier direction + sole language**: the SOURCE must be the dependent party ("X is OUR sole supplier"), never the supplier to the counterparty (#2 reversed). And the quote must contain explicit exclusivity ("sole", "single-source", "only source", "all of our X are purchased from") — major-vendor-without-exclusivity is not sole_supplier (#5).

**Acceptance (the labeled set is the test):** encode the 50 graded edges from `3b-grading-3.md` as offline fixtures. The guard set must **kill all 18 INCORRECT edges and retain ≥30 of the 32 CORRECT edges**; report every collateral kill by name with the guard responsible. These fixtures are frozen — they may not be edited to make guards pass.

### Ticket 3c-prep-2 — re-filter + re-sample (STOP after this)
- Apply the full guard stack (rounds 1–3) to **all 373 edges** in the graph. Quotes are retained, so this is mostly a metadata pass; re-fetch cached doc text where a guard needs more context. LLM use only if genuinely unavoidable, **≤$5 Haiku cap**, token-logged.
- Report: edges before/after, kills by guard, surviving counts by type, named/anonymous split before/after (G3 will move some named → anonymous).
- Regenerate the precision sample: **50 named edges, seed 3004** (pre-registered in `reports/3b-grading-3.md`) → `reports/3b-precision-sample-4.md`. If fewer than 50 named edges survive, sample ALL survivors and say so plainly.
- **Do NOT grade it. Do NOT build 3c/3d trading code.** The reviewer grades against the same ≥90% bar; shot 7 unfreezes on a pass.

## Hard rules
1. STOP after the sample. No 3c/3d, no Phase 5 changes, no real orders, no funding.
2. Race/shadow/fade lanes, exits, risk, PIT, existing tests untouched.
3. Guard patterns pre-registered in code before the re-filter; fixture edges frozen.
4. LLM spend: ≤$5 total this shot, token-logged.
5. Honest reporting: collateral kills, band gaps, and anything that surprises you go IN the report, not around it.

## Completion report (`reports/shot6-report.md`)
Files+tests (count before/after + tail); fixture-set results (18/18 killed, N/32 retained, collateral list); re-filter table (kills by guard, edges/named before→after); confirmation `3b-precision-sample-4.md` exists (seed 3004, ungraded); 4b-3 evidence (summary-strip screenshot-equivalent HTML or text dump, glossary live, template tests); rider status; deviations; open questions.

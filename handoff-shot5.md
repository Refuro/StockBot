# Shot 5 Handoff — Phase 2.5 (fade side) + Frontier graph regrowth + 50-edge precision sample

End-to-end build agent brief, **shot 5 only**. Reviewer audits at the end. Bar set by `reports/shot1..4-report.md`.

## Required reading
1. [implementation-plan.md](implementation-plan.md) — **Phase 2.5 rows 2.5a/b/c and its phase gate (the 40-signal falsifier, enforced as written)**, the shot-4 review block, conventions
2. [reports/3b-grading.md](reports/3b-grading.md) + [3b-grading-2.md](reports/3b-grading-2.md) — the guard taxonomy your regrown graph must satisfy
3. `bots/proportionality/` — the fade side joins THIS strategy class; reuse the ledger/decision/exit plumbing, don't fork it

## Environment facts
- Suite **212 passed** — stays green; never modify existing tests.
- Paper race + shadow week are LIVE on the daily 17:00 tick. **Do not disturb them**: no behavior changes to existing lanes, `paper_run` semantics, the live feed's cursor, or `RiskEngine`/PIT/exits. New lanes follow the shadow pattern: config-gated, additive, same single process.
- LLM router/caps as shipped (2c-F1 headroom + JSON mode). Runtime models per plan: Haiku triage → Opus+GPT ensemble + the **mandatory adversarial counter-pass**.
- Robinhood/Alpaca brokers exist but are NOT in scope — zero real orders, zero changes under `core/broker/`.

## Scope — build ONLY this, in this order

### Ticket R1 (riders, first, small)
- **gpt-5.5 price row**: if the user has supplied the settled June OpenAI reading, run `scripts/reconcile_llm_spend.py` and recalibrate per the standing rule (estimates high, never knowingly low), citing the measurement. If not supplied, skip and say so in the report.
- **Weekly `rh_auth status` scheduled task** (e.g. Mondays 08:45, logs to `logs/`), warning loudly when the token store is missing/expired — the pilot must never discover dead auth at order time.
- Delete `data/shadow_demo/` (reviewer-approved).

### Ticket 2.5a — crash detection + source document (mechanical)
- Detector on the daily tick's data: close-to-close or open-gap drop **≥10%**, universe band $300M–$10B with >$1M ADV (the expanded universe file + its metadata covers this; document any band gap honestly).
- For each crash: fetch the **primary source document** (8-K, prospectus/offering terms, downgrade substance, court/agency order) via the existing cached EDGAR plumbing — never a headline. Emit a typed `CrashEvent` with doc reference.
- Acceptance: fires on known historical crash days from the real dataset (pick ≥10 labeled examples across 2022–2026); correct source doc retrieved for ≥90% of the labeled sample; offline fixture tests.

### Ticket 2.5b — proportionality rubric + adversarial counter-pass (the brain)
- Rubric prompt: estimate the **fundamental dollar delta** (mechanical anchors where possible: guidance change × multiple, contract value, fine/judgment size, dilution amount) vs the **market-cap change**. Output: proportionate / overreaction (fade candidate) / underreaction, with stated probability of 10-day reversion.
- **Hard skips in code, not prompt-only**: binary-resolution situations (trial verdicts pending, fraud allegations, going-concern) auto-skip; forced-flow context (index exclusion, fund liquidation) flagged as strongest setups.
- **Mandatory counter-pass**: second call — "argue this crash is CORRECT" — to the OTHER model (GPT if Opus graded, per the plan's anti-self-confirmation rule). If the counter-pass is persuasive (rubric score drops below threshold on re-read), the fade is vetoed and the veto logged as a decision row.
- **Pre-register BEFORE scoring any validation data** (constants in code, like 2c): fade entry threshold on stated reversion probability; validation label = sign of 10-day net return post-crash; sample = crashes **2026-02-01 onward only** (post-cutoff honesty rule); spend cap **$25**.
- Acceptance: on the post-cutoff labeled sample, rubric-graded fades separate reverters from continuers directionally (report n and means plainly — weak is reportable, silence is not).

### Ticket 2.5c — fade entries in the SAME strategy class → paper lanes
- `ProportionalityRideStrategy` grows the fade side (rename only if clean): graded overreactions only; **hold 2–10 days, −8% hard stop, 10-day time-stop, ≤3% position, max 2 concurrent fades**; bucket `CalibrationLedger.strategy_bucket("crash","fade")` = `"crash:fade"`, real stated probabilities.
- Lanes (config-gated, additive, like shadow): fades go live in the existing gated lane (one engine, two trade types — the plan's design) **plus a mechanical fade control twin** (`crash_fade_mech`: fades EVERY detected ≥10% crash, constant p=0.55, same exits) so the rubric's value is measurable brain-vs-coin, mirroring the ride race.
- The 40-signal falsifier is race-time, not build-time: ledger counts fade signals; nothing to gate at build. Dashboard lanes appear automatically; verify the calibration table shows `crash:fade` separately.
- Acceptance: offline end-to-end test (synthetic crash → rubric stub → fade entry → exit paths incl. both stops); one manual tick on a scratch copy (shadow-demo pattern) showing the new lanes alive without disturbing production state.

### Ticket 3c-prep — graph regrowth + the 50-edge sample (STOP after this)
- Re-run the frontier graph pipeline over the **expanded 1,376-name universe** (latest 10-K each; resume state makes re-scans cheap; all six guards + dedupe active). Budget: **$10 Haiku cap** (≈13× the 105-name run's $0.21 plus margin), token-logged.
- Regenerate the precision sample: **50 named edges, seed 3003** (pre-registered here, new seed per protocol) → `reports/3b-precision-sample-3.md`. Report edge counts by type, named/anonymous ratio, resolution rate.
- **Do NOT grade it. Do NOT build 3c/3d trading code.** The reviewer grades against the same ≥90% bar; shot 6 unfreezes on a pass.

## Hard rules
1. STOP after 2.5c + the sample. No 3c/3d, no Phase 5 changes, no real orders, no funding.
2. Existing lanes/exits/risk/PIT/tests untouched; new lanes additive and config-gated; one tick process.
3. All fade theses/calibration rows: `bot_id` per lane, `strategy_bucket="crash:fade"`, prompt_version recorded; counter-pass result logged on EVERY fade decision (entered or vetoed).
4. LLM spend: $25 validation cap + $10 graph cap + runtime inside the existing $5/day tick cap; all token-logged.
5. Pre-registration discipline: thresholds and labels in code before any validation data is scored; if you tune after seeing results, stop and write it down instead.

## Completion report (`reports/shot5-report.md`)
Files+tests (count before/after + tail); 2.5a labeled-sample hit rate + doc-retrieval rate; 2.5b validation table (n, reverter/continuer means, vetoes fired) + the pre-registered constants; 2.5c lane evidence (scratch tick) + dashboard `crash:fade` row; graph regrowth stats + confirmation `3b-precision-sample-3.md` exists (seed 3003, ungraded); rider status; deviations; open questions.

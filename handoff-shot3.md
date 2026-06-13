# Shot 3 Handoff — 3a-F (graph guards) + U1 (universe expansion) + 2c (LLM quality scoring) + 2d (forward paper starts)

You are the end-to-end build agent. This brief covers **shot 3 only**. A reviewing agent audits at the end; build honestly, not to make a gate pass. Shots 1–2 set the bar — read `reports/shot1-report.md` and `reports/shot2-report.md`.

## Required reading (before any code)
1. [implementation-plan.md](implementation-plan.md) — Phase 2 rows 2c/2d, ticket U1, the 3b row, conventions block
2. [reports/3b-grading.md](reports/3b-grading.md) — the failure taxonomy ticket 3a-F implements
3. [strategy-synthesis.md](strategy-synthesis.md) — the Proportionality Engine contract and the backtesting honesty rule

## Environment facts
- Windows 11, venv `.venv`; suite currently **99 passed** — stays green; never modify existing tests.
- Keys in `.env` (never print values): Tiingo (**paid tier now** — IEX intraday endpoints available), Alpha Vantage (**premium this month** — high request rate; still batch politely), SEC_USER_AGENT, ANTHROPIC_API_KEY.
- **OPENAI_API_KEY is valid but the account has insufficient quota (429)** as of 2026-06-11. The user has been asked to fund it. Build the ensemble for both providers; degrade gracefully (below) if it is still unfunded at runtime.
- Three scheduled tasks already run daily (ingestion 08:00, crumbs 08:30, flow lens 09:00).
- 2b gate: **PASSED** (pre-committed criteria, see plan). 3b checkpoint: **FAILED at 54%** — which is why 3a-F exists and why 3c/3d stay frozen.

## Scope — build ONLY this, in this order

### Ticket 3a-F — graph extraction guards (fix, then STOP for re-grade)
Implement the five code-side guards from `reports/3b-grading.md` §"Failure taxonomy" exactly:
1. revenue-basis guard (reject accounts-receivable contexts),
2. aggregate-group guard,
3. operative-decree guard (filer-as-party + reject proposed/discussing/satisfied/terminated),
4. supplier-direction guard ("products from X" ≠ customer),
5. anonymous-placeholder guard ("Customer C" is not named).

Plus entity-normalized dedupe (EPA = Environmental Protection Agency; Caremark LLC = CVS Caremark — normalize via casefold/suffix-strip/alias map, keep it conservative). Each guard gets a test using the failing passages from the graded sample as fixtures. Then: re-run the pipeline (resume state makes refetch free; fresh Haiku confirmation is pennies), regenerate the sample with **seed 3002** (pre-registered in the grading doc), write `reports/3b-precision-sample-2.md`. **Do not grade it. Do not build 3c/3d.** The reviewer re-grades against the same ≥90% bar.

### Ticket U1 — mechanical universe expansion (before 2d, so paper trades the wide universe)
Spec is in the plan (status section). Summary: screener script → every US-listed name with $300M–$3B market cap (AV `OVERVIEW`), median dollar-ADV ≥ $500k (computed from prices), primary exchanges, no hand-curation. Target ~500–1,000 names. Then full cache + dataset build, and one mechanical drift-study re-run on the expanded universe written to `reports/2b-drift-report-expanded.md` as a **declared secondary** (the 2b gate stays decided on the original pre-registered run — do not touch that report or the study cells). Document screener reproducibility (seed/date/inputs) and the remaining survivorship caveat.

### Ticket 2c — LLM quality scoring (the brain, finally)
**What it scores:** for each qualifying earnings surprise, durability of the beat (real demand vs one-offs: tax items, settlements, buyback EPS effects, pulled-forward orders) and guidance walk-backs, from the 8-K text (and exhibit/press-release text where attached). Output: a quality score + a **stated probability** for "this name drifts up over the hold window," replacing the mechanical constant 0.55.
- **Pipeline:** Haiku triage (cheap read of the 8-K; kills obvious junk) → **Opus 4.8 + GPT-5.5 ensemble** for survivors. Disagreement is a signal: when the two models' probabilities diverge by >0.15, cap the stated probability at the lower one and flag `ensemble_disagreement` in the thesis rationale.
- **Degradation rule (pre-registered):** if OpenAI is still unfunded at runtime, run Claude-only, tag every such thesis `ensemble_unavailable`, and cap stated probability at 0.60. Never silently single-model.
- **Validation (the honesty rule applies):** all runtime models have training cutoffs ≈ Jan 2026. The labeled sanity check uses ONLY events from **2026-02-01 onward** (post-cutoff): label = sign of the 10-day net return after the event (mechanical, defined before scoring). Acceptance: on that sample, mean quality score of drifters > mean score of faders, stated plainly with sample size (expect n≈50–150 from the expanded universe — report whatever it is). This is a sanity check, not proof; forward paper is the proof. **Spend cap for the validation study: $25, logged.**
- LLM calls live in `core/llm/` behind a router; prompts versioned (`prompt_version` recorded on every decision row — the schema field already exists).

### Ticket 2d — entry/exit wiring + REAL live feed + forward paper starts
- **Exits (from the plan, fixed):** profit-take 50% of the position at +15%; 7% trailing stop; −8% hard stop; 14-day time-stop. Entries: 2b's mechanical signal gated by 2c's quality score (pre-register the threshold before looking at paper results; document it).
- **A real live `DataFeed`** (the current paper feed is fake): daily cadence is fine — Tiingo EOD/IEX prices, SEC submissions polling for new 8-K Item 2.02s, AV earnings refresh for names that just reported. Same `DataFeed` interface, PIT-trivial (now is now), `get_events` cursor semantics preserved. Do not touch the historical feed.
- **Paper runs as a 4th scheduled task** (after market close, e.g. 17:00 ET equivalent local): one `drive_strategies` fan-out tick per trading day on the shared paper DB — the proportionality strategy with real 2c probabilities, plus (cheap, valuable) a **mechanical-only twin** with the constant 0.55 as the control lane (`bot_id="proportionality_mech"`). The race between them is the cleanest measure of whether the LLM layer adds anything.
- Theses/calibration: real stated probabilities, `strategy_bucket="earnings:ride"`, resolved on realized round trips — the ledger machinery from 2b is already there.
- **Runtime spend cap: $5/day**, enforced in code, spend logged per run. Daily log under `logs/paper-YYYY-MM-DD.log`.
- Acceptance: the paper task runs end-to-end unattended on a real trading day (or a verified dry-run if launched on a weekend), records snapshots/theses/decisions, and the dashboard shows both lanes.

## Hard rules
1. **STOP after 2d is running.** No 2.5 (fade side), no 3c/3d (frozen pending 3b re-grade), no Phase 5 (no live broker code, no real orders anywhere).
2. `RiskEngine`, PIT guards, existing tests, the original `drift_study.py` cells and `reports/2b-drift-report.md`: untouched.
3. **The LLM never sizes an order, never bypasses `submit_and_record`, never touches the risk engine.** It produces scores/probabilities that feed thesis records; deterministic code does everything else.
4. Pre-register before peeking: the 2c quality-score entry threshold and the validation label definition are written down in code/docs BEFORE the validation sample is scored. If you find yourself adjusting either after seeing results, stop and write it in the report instead.
5. Every LLM call: spend-capped, token-logged, model id + prompt_version recorded.
6. New code gets tests (offline/stub LLM fixtures). Suite stays green. Blocked = document and stop; never improvise around a gate.

## Completion report (`reports/shot3-report.md`)
- Files added/changed (one line each); test count before/after + suite tail
- 3a-F: guards implemented, fixture tests, confirmation `reports/3b-precision-sample-2.md` exists (seed 3002), edge counts before/after guards
- U1: screener definition, final universe size, cache/dataset stats, expanded-universe drift table (stated plainly), Tiingo/AV spend
- 2c: validation-sample results (n, drifter vs fader mean scores, the pre-registered threshold), total LLM spend, ensemble vs degraded mode status
- 2d: paper task name/schedule, first run log excerpt, both lanes visible in dashboard, the pre-registered entry threshold
- Deviations with reasons; open questions for the reviewer

# Shot 1 Handoff — Phase 2a + 2b (Proportionality Engine, ride side)

You are the end-to-end build agent for this repo. This document is your brief for **shot 1 only**. A reviewing agent will audit your work at the end; your job is to build honestly, not to make a gate pass.

## Required reading (in order, before any code)
1. [implementation-plan.md](implementation-plan.md) — especially Phase 2 and the **conventions block at the bottom** ("Conventions for whoever builds Phase 2")
2. [strategy-synthesis.md](strategy-synthesis.md) — the why behind the architecture

## Environment facts
- Windows 11, venv at `.venv` — run tests with `.\.venv\Scripts\python.exe -m pytest tests/ -q`
- Current suite: **50 passed.** It must stay green. Do not modify existing tests to make new code pass.
- API keys are live in `.env` and verified working — run `scripts/smoke_test_keys.py` to confirm (Tiingo ✅, Alpha Vantage ✅, SEC EDGAR ✅).
- **Alpha Vantage free tier = 25 requests/day**, one request per ticker for full earnings history, responses cached to disk by `CachedHTTP`. Therefore: **kick off ingestion on day one, design it resumable** (the cache already makes re-runs free), and build while the cache fills. A 100-ticker universe takes ~4 days of cache accumulation.

## Scope — build ONLY this

### Ticket 2a — Earnings watcher + standardized surprise (mechanical, no LLM)
Productionize the earnings-event path on top of what exists (`stockbot/data/real_loader.py` already computes real Item-2.02 surprises; `EarningsBaselineStrategy` is the skeleton). Acceptance: surprises match hand-computed values on known earnings events (extend the existing test pattern).

### Ticket 2b — 🚦 Mechanical drift backtest on REAL data (the kill checkpoint)
- **Universe:** target ~100 names, $300M–$3B market cap, ADV ≥ $500k. Start with whatever subset the AV cache has and expand daily; document the final universe and its survivorship caveats (use the loader's survivorship metadata).
- **Data:** ≥3 years of real daily prices + real earnings surprises through the existing `HistoricalReplayFeed` path. Point-in-time discipline is enforced by the feed — never work around it.
- **Pre-registered primary test (do NOT tune after seeing results):** long top-decile positive surprises, 10-day hold, `next_bar` execution, `small_cap` cost profile. Secondary cuts for the report: surprise thresholds (>5%, >10%), hold horizons (5/10/20d), by year, hit rate + mean net return + sample size per cell.
- **Output: a drift report, not a verdict.** Write `reports/2b-drift-report.md` with the tables, the exact reproduction command, and the universe definition. **You do not decide whether the gate passes.** The reviewers do.

### Bundled infrastructure (gate-free, do after 2a/2b are clean)
- Phase 4a: multi-strategy fan-out — N `Strategy` instances with distinct `bot_id`s on one shared feed in `harness/paper.py`
- Phase 4b: minimal dashboard reading the state DB (equity curves, per-`strategy_bucket` calibration, slippage realized-vs-assumed)

## Hard rules
1. **STOP after 2b.** Do not build 2c (LLM quality scoring) or 2d (entry/exit wiring). The checkpoint only works if the work it gates doesn't exist yet.
2. All orders go through `StrategyContext.submit_and_record` — risk is enforced centrally there. Never bypass it, never weaken `RiskEngine`.
3. Tag every thesis/calibration row: `strategy_bucket=CalibrationLedger.strategy_bucket(event_type, direction)` (e.g. `"earnings:ride"`), `bot_id="proportionality"`.
4. Metrics come from the persisted DB (`compute_backtest_metrics`), never from in-memory broker state.
5. New code gets tests. Suite stays green. If genuinely blocked, document the blocker in your report and stop — do not improvise around a gate or a data problem.
6. Do not touch: existing tests, `RiskEngine`, the PIT guards, `strategy-*.md` docs.

## Completion report (required, as `reports/shot1-report.md`)
- Files added/changed and why (one line each)
- Test count before/after; full-suite output tail
- The 2b drift report summary table (the numbers, stated plainly — including if they're bad)
- Universe actually used + survivorship caveats + AV cache coverage
- Any deviations from this brief or the plan, with reasons
- Open questions for the reviewer

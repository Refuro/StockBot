# Implementation Plan — Two Engines and a Lens

Build the synthesized design from [strategy-synthesis.md](strategy-synthesis.md): a **Proportionality Engine** (one strategy that classifies every shock as underreaction → ride, proportionate → no trade, or overreaction → fade), a **Frontier Engine** (trade the security an information wave hasn't reached, only where materiality is *externally certified* — a home-market price print or a disclosed revenue dependency), plus a **Flow Lens** measurement harness and a **Crumbs** scanner outside the trading loop. Validate against historical data, race ledger *buckets* in a shared paper harness, graduate winners to live.

> **Naming continuity:** earlier drafts called these "Bot A (PEAD)" and "Bot B (Propagation)." Bot A *is* the Proportionality Engine's ride side — Phase 2 is unchanged. Bot B evolved into the Frontier Engine — Phase 3 is rescoped. The competition unit is now the **calibration-ledger bucket** (engine × event type × direction), not the bot: capital is allocated and killed per bucket.

This doc is organized as **phases**. Each phase has: goal, what you build, which **build-time agent** writes it, which **runtime model** the bot calls, and a hard **exit criterion** before moving on.

---

## The one architectural rule everything hangs on

**One decision codebase, three execution modes.** The bot's logic must not know or care whether it's running against historical replay, live paper, or live real money. That's achieved with two interfaces:

```
DataFeed   (historical_replay | live)     → gives the bot market data + events as-of a timestamp
Broker     (simulated | robinhood | alpaca) → takes orders, returns fills
```

The decision logic sits in the middle and is identical in all modes. **Backtest → paper → live is then a config flag, not a rewrite.** This is what makes "test it, then go live" real instead of a second project.

---

## ⚠️ The backtesting honesty problem (read before Phase 0)

You asked to test against historical data. You can — but only for *part* of each bot, and missing this distinction is how people fool themselves into trading a broken system.

**Two kinds of logic live in each bot:**

1. **Mechanical logic** — surprise calculation, drift measurement, the propagation trigger, liquidity filters, sizing, the slippage model, risk limits. **This backtests cleanly** against years of historical data. Validate it hard here.

2. **LLM-judgment logic** — "is this earnings beat *durable*?", "what's the *sign* of this second-order effect?". **This does NOT backtest cleanly.** Every Claude/GPT model has a training cutoff (e.g., Opus 4.8 ≈ Jan 2026). Ask it to reason about a 2024 event and it may *already know the outcome*. Your backtest will look brilliant and be worthless — pure look-ahead leakage.

**The discipline that keeps you honest:**

- **Point-in-time data only.** The `DataFeed` must hand the LLM *only what was knowable as of the simulated timestamp* — no future prices, no later filings. Enforced at the interface, not by trusting the prompt.
- **Backtest the skeleton, forward-test the judgment.** Use history to prove the mechanical engine, the sizing, and the slippage assumptions. Prove the *LLM's* edge with **forward paper-trading**, which is out-of-sample by construction — the future hasn't happened, so there's nothing to leak.
- **If you must historically test the LLM**, use only data *after* the model's training cutoff (a small, shrinking window), and treat it as a sanity check, not proof.

Bottom line: **historical data validates the machine; live paper validates the brain.** The plan below does both, in that order.

---

## Tech stack

- **Language:** Python 3.12. (Best ecosystem for market data, EDGAR parsing, broker SDKs, pandas backtesting.)
- **State store:** SQLite to start (single file, zero-ops), with a clean data layer so you can lift to Postgres if it grows.
- **Core libs:** `pandas`/`polars` (data), `pydantic` (typed thesis/event schemas), `httpx` (APIs), `apscheduler` (the loops), `anthropic` + `openai` SDKs (runtime models), `sec-edgar-downloader`/`edgartools` (filings).
- **Dashboards:** a small `streamlit` or `fastapi`+htmx app reading the state DB — for watching both bots and the calibration ledger.
- **Config:** one `.env` + a `config.yaml` that sets `MODE = backtest | paper | live` and per-bot model choices.

### Repo structure
```
stockbot/
  core/
    datafeed/       # DataFeed interface + historical & live impls (point-in-time enforced)
    broker/         # Broker interface + simulated, robinhood (MCP), alpaca impls
    state/          # SQLite schema: positions, cash, theses, events, calibration ledger
    risk/           # deterministic risk engine (caps, circuit breakers) — NO LLM
    llm/            # model router: cheap/strong tiers, ensemble, prompt templates
    calibration/    # Brier scoring, per-bucket gating
  bots/
    proportionality/  # Engine 1: ride + fade sides (renamed from pead/ ✅)
    frontier/         # Engine 2: concentration-links + ADR-lag stages (renamed from propagation/ ✅)
  tools/
    flow_lens/        # synthetic-crowd logger + overlay (NOT a Strategy; no capital)
    crumbs/           # odd-lot tender scanner cron (notifier, not a bot)
  harness/
    backtest.py     # historical replay runner
    paper.py        # champion/challenger live-paper runner (all strategies, shared feed)
    metrics.py      # Sharpe, hit rate, drawdown, slippage-adjusted return
  dashboard/
  config.yaml
```

---

## Which agent builds what, and which model the bot runs

Two different questions — keep them separate.

### Build-time (the coding agent that writes the code)

| Component | Build agent | Why |
|---|---|---|
| Scaffolding, data adapters, watchers, API plumbing, dashboards, glue | **Cursor Composer 2.5** | Fast (<30s iterations), cheap (~1/10 Opus cost), excels at tool-heavy CRUD/plumbing churn. This is 60% of the lines and most of it is boring — let the cheap fast model grind it. |
| Backtest engine, multi-file wiring, the three-mode abstraction, refactors/migrations | **GPT-5.3-Codex** (or GPT-5.5) | Strongest at long-horizon "messy multi-part task, plan and keep going" autonomous work; context compaction handles big refactors. |
| Risk engine, calibration math, the LLM prompt design, financial-logic correctness, architecture calls | **Claude Opus 4.8** (Claude Code) | The correctness-critical pieces where a subtle bug = lost money. Strongest nuanced reasoning; best at the parts you can't afford to get subtly wrong. |

Rule of thumb: **Composer for volume, Codex for long autonomous builds, Opus for anything where being wrong costs money.**

### Runtime (the model the bot calls while trading)

| Loop / decision | Runtime model | Why |
|---|---|---|
| Slow-loop reading: triage filings, scan events, extract graph edges (high volume, cheap) | **Claude Haiku 4.5** | Fast, cheap, runs over thousands of documents. 90% of raw events die here. |
| Proportionality Engine: reaction-vs-fundamental judgment (ride/nothing/fade) — incl. surprise *quality* (durable vs. hollow) and crash *materiality* | **Claude Opus 4.8**, cross-checked by **GPT-5.5** | Money decision. Use *both* and treat disagreement as a confidence signal — only size up when they agree. On the fade side, add a mandatory counter-pass ("argue this crash is *correct*") to fight agreeable-model bias. |
| Frontier Engine: relationship mapping + lag thesis | **Claude Opus 4.8**, cross-checked by **GPT-5.5** | Materiality itself is externally certified (price print / disclosed dependency) — the LLM only maps the link and frames the thesis. |
| "Has the market already priced this?" adversarial check | **GPT-5.5** | Deliberately use the *other* model than the one that formed the thesis, to fight self-confirmation. |
| Flow Lens: synthetic-crowd sampling (consumer-model picks) | **Deployed consumer models** (ChatGPT default, Claude, Grok, Gemini) at retail settings | The whole point is to sample what *retail agents* see — use their models, not your best one. ~$3/day. |

Note: Composer 2.5 and Codex are **build-time agents**, not runtime brains — you don't call them per-trade. Your runtime API spend is Haiku (bulk reading) + occasional Opus/GPT-5.5 (the few real decisions). The ensemble-disagreement-as-confidence trick is a genuine design edge — it's nearly free and directly feeds the calibration ledger.

---

> **How to read the phases below:** each phase is a *milestone*. Under it are **ticket-sized sub-tasks** (e.g., 1a, 1b) — each is one buildable unit with its own **build agent** (the coding agent that writes it), **runtime model** (the LLM the bot calls, if any), and a one-line **acceptance criterion**. Hand the coding agents one ticket at a time. The phase advances only when all its tickets' acceptance criteria pass plus the phase-level gate.
>
> **Agent shorthand:** **Composer** = Cursor Composer 2.5 (fast/cheap plumbing). **Codex** = GPT-5.3-Codex (long-horizon autonomous builds). **Opus** = Claude Opus 4.8 via Claude Code (money-critical correctness). Runtime: **Haiku** = Claude Haiku 4.5 (bulk reading), **Opus+GPT-5.5 ensemble** (money judgments), **GPT-5.5** (adversarial check).

---

## Phase 0 — Foundations ✅ DONE (verified June 2026)

**Built by Composer 2.5. Reviewed and test-verified — meets exit criterion.**

The three-mode spine exists: `DataFeed`/`Broker` interfaces + factories, `SimulatedBroker` with a correct half-spread slippage model, deterministic `RiskEngine` (position cap + daily-loss + drawdown halts), SQLite `StateStore`, `CalibrationLedger` (Brier scoring + `is_bucket_calibrated` gating: ≥10 samples & mean Brier ≤ 0.25), and a `HelloWorldBot` that runs through the whole stack.

**Exit criterion — MET:** `pytest tests/` → **9/9 pass**, including `test_hello_world_all_modes[backtest|paper|live]` and `test_same_bot_code_all_modes` (proves config-only mode switch). Point-in-time guard, slippage, risk cap, and calibration ledger each have passing tests.

---

## Phase 0.1 — Harden the foundation (do this BEFORE any bot) 🔧

**Why:** Phase 0 is correct but has three gaps that will bite in Phases 4–5 if left. These are hardening tickets, not bug-fixes — the spine works; it's just not yet durable enough to run for weeks or to reconcile against a real broker.

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 0.1a ✅ | **Portfolio/equity-curve persistence.** Add `StateStore.record_snapshot()` (writes `portfolio_snapshots` + `positions` — tables exist but nothing writes them); have the runtime persist a snapshot each tick. | **Opus** (state correctness) | none | **MET** — runtime persists a snapshot each tick; equity curve is queryable from the DB via `StateStore.get_equity_curve()` and `metrics.load_equity_curve()` reads it with no broker in memory. |
| 0.1b ✅ | **Broker reconciliation interface.** Define `Broker.sync_positions()` as the source-of-truth contract: `SimulatedBroker` restores from the last snapshot on startup; live brokers (Phase 5) will pull from the venue. | **Opus** (money-critical) | none | **MET** — `Broker.sync_positions()` added; `SimulatedBroker` restores cash + positions from the last snapshot; `test_restart_resumes_with_zero_desync` proves a killed/restarted run resumes with zero desync. |
| 0.1c ✅ | **Active point-in-time enforcement.** Make feeds call `validate_point_in_time()` *inside* `get_quote`/`get_events`, not just expose it. Add a leak test. | **Codex** | none | **MET** — feeds validate in-method; `test_pit_is_enforced_inside_get_quote_for_replay_feed` proves a future-dated record raises `PointInTimeViolation` from within `get_quote`. |
| 0.1d ✅ | **Decision/audit-log schema.** Extend `theses` (or a new `decisions` table) with `inputs_json`, `model_id`, `prompt_version`; add `StateStore.record_decision()`. This is the "most valuable asset" the strategy docs flagged. | **Composer** | none | **MET** — `decisions` table + `record_decision()`/`get_decision()`; hello-world records full inputs; `test_decision_is_fully_replayable_from_stored_inputs` proves replay from stored inputs. |
| 0.1e ✅ | **Run metadata + structured logging.** A `runs` table (run_id, started_at, config hash, model versions, git SHA) + file logging. | **Composer** | none | **MET** — `RunContext` writes a `runs` row + logfile per run; `test_run_writes_db_row_and_logfile` proves traceability to config hash and log output. |

**Phase gate — MET:** all five tickets' tests pass (0.1a–0.1e), restart-mid-run (0.1b) and point-in-time leak (0.1c) are green. Ready to build bots.

---

## Phase 1 — Historical data pipeline + backtest harness

**Goal:** replay years of market history through the spine, with point-in-time discipline enforced. *Mechanical logic only — no LLM yet.*

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 1a ✅ | **Historical `DataFeed`** — prices/volume (Polygon flat files or Tiingo) + historical EDGAR filings/earnings with *real filing timestamps*; enforces 0.1c PIT guard. | **Codex** | none | **MET (local CSV adapter)** — `HistoricalReplayFeed` replays prices/events as-of correctly with active PIT checks and explicit anti-peek guard (`get_quote_at` raises `PointInTimeViolation` for future timestamps). |
| 1b ✅ | **Backtest runner** (`harness/backtest.py`) — advances the clock, fans events to a bot, records fills + snapshots (via 0.1a). | **Codex** | none | **MET** — runner persists fills/snapshots end-to-end; a 3+ year baseline test is reproducible run-to-run and shows slippage drag. |
| 1c ✅ | **Metrics module** (`harness/metrics.py`) — Sharpe, hit rate, max drawdown, return net of slippage, turnover. | **Composer** | none | **MET** — `compute_backtest_metrics()` reads persisted snapshots/fills; metrics reproducible across reruns and slippage drag visible in `total_return` / `total_slippage_cost`. |

**Phase gate — MET (harness mechanics only):** dumb baseline backtests over 3+ years with reproducible metrics and slippage drag — **but on synthetic, deterministic, single-symbol data** (a rigged upward drift). This proves the *plumbing* (reproducibility, persistence, slippage accounting), **not edge and not real data.** Two blockers in Phase 1.5 must close before Phase 2's mechanical-drift checkpoint (2b) means anything.

---

## Phase 1.5 — Strategy abstraction + real data (BLOCKERS before Phase 2) 🚧

**Why:** A code review of the completed Phase 1 found two things that must close before Bot A, plus five smaller cleanups. The harness is well-built and point-in-time-honest, but (a) the strategy logic is currently trapped inside the backtest harness, and (b) everything green so far ran on synthetic data. Both undermine Phase 2 if left.

### Blocker tickets

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 1.5a ✅ | **Extract a `Strategy` protocol — the core "one codebase, three modes" fix.** Today `harness/backtest.py` is hardcoded to `HelloWorldBot` (`raise TypeError` otherwise) and the earnings-baseline trading logic is *inlined in the harness* (`_run_earnings_baseline_backtest`), bypassing the bot entirely. Define a `Strategy` interface — e.g. `on_tick(ctx)` where `ctx` exposes the as-of `DataFeed`, `Broker`, `StateStore`, and risk engine — and make `backtest.py`, `paper.py`, and the (future) live loop all drive *the same* strategy object identically. Reimplement the earnings baseline as a `Strategy` to prove it. | **Opus** (architecture/correctness) | none | **MET** — `Strategy`/`StrategyContext` in `core/strategy.py`; single loop `harness/loop.py:drive_strategy` drives both `backtest.py` and `paper.py` (no trading logic in the harness). `EarningsBaselineStrategy` runs unchanged under `run_backtest`/`run_paper` with identical fills + equity curves (`test_same_strategy_identical_decisions_backtest_vs_paper`). `HelloWorldBot` and the baseline both subclass `Strategy`. All prior tests green (28 total). |
| 1.5b ✅ | **Real historical-data loader.** `HistoricalReplayFeed` is BYO-CSV; there is no real ingestion. Build loaders that pull **prices/volume** (Polygon flat files or Tiingo) and **earnings events with real filing timestamps** (EDGAR 8-K / earnings calendar) into the CSV/feed format, with point-in-time timestamps preserved. Include a small reusable downloader + a fixtures cache so tests can run offline. | **Codex** (long-horizon, data-heavy) | none | **MET (timestamps only — see 1.5h)** — `stockbot.data.ingest_real` pulls Tiingo daily prices + SEC submission timestamps into feed CSVs, writes survivorship metadata, supports cache/offline mode. `test_offline_real_loader_builds_3y_dataset_and_backtests` + `test_real_loader_output_respects_point_in_time` pass offline. **Caveat found in review:** it labels *every* 8-K as `earnings` and hardcodes `surprise_pct=0.06` — real filing *times* but a **fake surprise signal.** Closing that is ticket 1.5h. |
| 1.5h ✅ | **Real earnings-surprise signal (BLOCKS Phase 2b).** The loader fakes the one input Bot A is built on. Replace the hardcoded `surprise_pct=0.06` with a **real standardized surprise** (actual vs. consensus EPS) and filter 8-Ks to genuine earnings releases (Item 2.02 / results filings), not all 8-Ks. Needs an estimates/actuals source (e.g. a fundamentals/earnings-estimates API) joined on ticker + period, with point-in-time consensus (the estimate as it stood *before* the release). | **Codex** (data-sourcing) | none | **MET** — loader now joins SEC 8-K Item 2.02 filings to Alpha Vantage quarterly earnings (`reportedEPS`, `estimatedEPS`) and computes `surprise_pct = (actual-consensus)/|consensus|`; events without a match are dropped (no fake constants). Payload includes `actual_eps`, `consensus_eps`, and PIT marker `consensus_as_of` (release minus 1s). `test_real_loader_uses_item_202_and_computes_real_surprises` verifies only Item-2.02 events are tagged `earnings`, surprises vary, and hand-checked surprises (+0.10 and −0.08) are present. |
| 1.5i ✅ | **Re-enforce the risk engine in the Strategy loop (regression from 1.5a).** The 1.5a refactor left `RiskEngine` orphaned — `EarningsBaselineStrategy`/`drive_strategy` never called `check_order`/`update_equity`, so position caps and daily-loss/drawdown halts were bypassed on the new loop. | **Opus** (safety-critical) | none | **MET** — risk is now enforced *centrally* in `StrategyContext.submit_and_record` (entries gated by `check_order` + `record_order`; exits pass through so positions can close during a halt) and `drive_strategy` calls `risk.update_equity` each tick. `test_phase15_risk.py` proves an over-cap entry is REJECTED with no fill on the loop, and a within-cap entry passes. Full suite green (39 passed). |

### Smaller cleanups (track now, fix before they bite)

| # | Task | Build agent | Acceptance criterion |
|---|---|---|---|
| 1.5c ✅ | **Sharpe annualization.** `sharpe_ratio` hardcodes 252 periods/yr and assumes one snapshot ≈ one trading day — correct only for the current daily synthetic data; silently wrong on intraday/irregular cadence. | **Opus** (metric correctness) | **MET** — `metrics.infer_periods_per_year` derives annualization from the **median spacing of actual snapshot timestamps** (calendar-based); `compute_backtest_metrics` infers it per run (overridable) and surfaces it as a `periods_per_year` field. `test_periods_per_year_inferred_from_snapshot_cadence` checks daily/hourly/minute cadence; `test_intraday_sharpe_annualizes_correctly` confirms the same return series annualizes by `sqrt(periods ratio)` across cadences. 32 tests green. |
| 1.5d ✅ | **Next-bar execution option.** Fills currently occur on the *same* bar as the signal (enter at the event's own timestamp price) — slightly optimistic vs. reality. | **Codex** | **MET** — added `simulated_broker.execution_timing` (`same_bar` or `next_bar`) and wired it through broker factory/config. In `next_bar` mode, submitted orders are queued and settled at the first quote timestamp **strictly after** the signal (`ctx.settle_pending_orders` + broker `process_pending_orders`). Default is `next_bar` in `config.yaml` for realism. Tests: `test_next_bar_execution_fills_on_first_quote_after_signal` and `test_same_bar_execution_is_selectable`; full suite green (34 passed). |
| 1.5e ✅ | **Cost-model realism.** Flat 0.3% slippage, zero spread/impact/partial-fill — and small-cap slippage is the #1 risk in the strategy docs. | **Opus** (financial logic) | **MET** — `SimulatedBroker` now supports `cost_profile` (`flat`, `small_cap`, `robinhood_retail`) plus per-symbol overrides via `symbol_costs` (`spread_bps`, `adv_shares`, `impact_coeff_bps`). Effective slippage is derived from one-way spread + participation impact (`sqrt(order_qty/ADV)`) and persisted per fill. Added a Robinhood-oriented retail profile in `config.yaml` (explicitly documented as an approximation), while keeping model inputs overridable per symbol. `test_cost_model_scales_slippage_for_liquidity` validates thin-name slippage > liquid-name slippage; full suite green (35 passed). |
| 1.5f ✅ | **`get_events` cursor.** Returns all visible events every tick (O(n²) over long runs); baseline dedupes via a growing set. | **Codex** | **MET** — `HistoricalReplayFeed` and fake feeds now implement incremental event cursors: `get_events()` returns only newly visible events since the prior call (or explicit window via `since=`), backed by an index rather than scanning full history every tick. `test_historical_datafeed_events_use_incremental_cursor` validates cursor semantics. |
| 1.5g ✅ | **Survivorship/universe handling.** Feed silently omits delisted names. Tied to 1.5b. | **Codex** | **MET** — `DataFeed` now exposes `get_universe(as_of)` and `HistoricalReplayFeed` applies point-in-time listing windows from optional `listed_at`/`delisted_at` columns in `prices.csv`. Real loader writes lifetime metadata/columns, and `test_historical_feed_universe_respects_listing_and_delisting` verifies delisted symbols disappear only after `delisted_at` (not dropped retroactively). |

**Phase gate — MET:** 1.5a + 1.5b + **1.5h** + **1.5i** green, all prior tests pass, and the earnings baseline can re-run on ≥3-year data with **real, varying surprises**. 1.5c–1.5g done.

---

## Workstream F — Flow Lens + Crumbs (START NOW, parallel to everything) 🆕

**Why now:** both live *outside* the trading loop, depend on nothing in flight, and the Flow Lens dataset is time-zero-valuable — every unlogged week of the early agentic era is unrecoverable. Neither touches capital.

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| F1 ✅ | **Flow Lens logger** (`tools/flow_lens/`) — daily cron queries consumer models (ChatGPT default, Claude, Grok, Gemini) with ~10–15 retail prompt archetypes at deployed settings; logs every ticker mentioned with model/prompt/timestamp to the state DB; computes per-ticker **consensus score** + **novelty flag** (newly entering consensus). | **Composer** (~200 lines + cron) | consumer models (~$3/day) | **MET** — `stockbot.tools.flow_lens` samples 12 retail archetypes × 4 consumer models, persists rows to `flow_lens_samples` + `flow_lens_consensus` (model version per sample), computes consensus/novelty vs prior day. CLI: `stockbot-flow-lens run` (offline stub) and `stockbot-flow-lens schedule` (APScheduler cron). Tests: `tests/test_flow_lens.py` (4 passed). |
| F2 ✅ | **Flow validation study** — join F1 logs against free retail-flow proxies (FINRA off-exchange volume files); test H1: *do small/mid-cap names newly entering multi-model consensus see abnormal volume over the next 1–5 days?* | **Codex** (data join + stats) | none | **MET (tooling + report path)** — `flow_lens study` now ingests FINRA CSV proxies, joins novelty-onset symbols from F1, computes 1–5 day abnormal-volume effect sizes (mean/median/positive-rate), and emits a written markdown answer. Unit tests validate positive-effect detection on synthetic data. **Live conclusion still requires 4–8 weeks of accumulated F1 logs.** |
| F3 | *(gated on F2)* **Flow overlay** — expose consensus state to both engines via `StrategyContext`: defer fades on consensus-onset names; tighten exits at saturation. Later still: graduate to its own `Strategy` only with its own calibrated bucket. | **Opus** (touches trade decisions) | none | Overlay decisions logged + ledger-bucketed separately so its marginal value is measurable (overlay-on vs overlay-off variants in Phase 4). |
| F4 ✅ | **Crumbs scanner** (`tools/crumbs/`) — daily EDGAR scan of SC TO-I / 13E4 / SC TO-T filings for odd-lot priority clauses; notify (no auto-trading; 99-share positions are manual by design). | **Opus** (built) | regex-first (Haiku clause extraction = later layer) | **MET (v1)** — `tools/crumbs/scanner.py` queries EDGAR full-text search per form, matches odd-lot phrases, dedupes by accession, emits alerts (company, form, accession, date, link) + a "verify by hand" notice; offline fixture tests pass (`test_strategy_bucket_and_crumbs.py`). v1 alerts carry metadata + link, **not yet** parsed tender price/deadline — that's the Haiku layer, optional. Run via `python -m stockbot.tools.crumbs.scanner` (needs only `SEC_USER_AGENT`). |

---

## Phase 2 — Proportionality Engine, ride side (formerly "Bot A: Refined-PEAD") — UNCHANGED

**Goal:** the cash-flow engine — validated mechanically on history, then forward-paper-tested for its LLM judgment. Note the **kill-checkpoint at 2b** before any LLM work. *(Synthesis note: this work is unchanged; it simply becomes the ride side of the Proportionality Engine. Build exactly as specified.)*

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 2a ✅ | **Earnings watcher + standardized surprise calc** (mechanical). | **Composer** (watcher) + **Opus** (surprise math) | none | **MET** — `bots/proportionality/surprise.py` is the single canonical surprise computation (`(actual−consensus)/|consensus|`, None when undefined — never a fake constant); `watcher.py` validates raw feed events into typed `EarningsSurprise` records, RECOMPUTING the surprise from `actual_eps`/`consensus_eps` (stored `surprise_pct` is cross-checked, never trusted). Real loader now uses the same function. Hand-computed values verified in `tests/test_phase2a_surprise.py` (+10%, −8%, negative-consensus +50%, undefined cases). |
| 2b ✅ | **🚦 CHECKPOINT — mechanical backtest** of surprise+liquidity filtering *with no LLM.* | **Codex** | none | **GATE PASSED (2026-06-11, reviewer call against pre-committed criteria).** Criteria were fixed before full data arrived: PASS = primary cell holds positive mean net return AND >50% hit on the full universe. Result on 100/105 names (premium AV key collapsed the cache wait; the 5 dead no-CIK tickers stay excluded per pre-commitment): **174 closed trades, 60.9% hit, +5.67% mean net/trade, every year's mean positive** (`reports/2b-drift-report.md`). Secondary threshold cells corroborate on larger samples (52–57% hit, +1.7–2.9% mean, 372–877 trades). **Reviewer caveats recorded with the pass:** survivorship bias flatters these numbers; the 4.94 Sharpe is inflated by cash drag ($4k positions on a $100k account) — the per-trade stats are the meaningful ones; AV consensus PIT integrity unverified. None of these survive into forward paper, which is exactly what 2c/2d start. **2c/2d are unblocked.** |
| 2c ✅ | **LLM quality-scoring** — score durability of the beat + detect guidance walk-backs from transcript/8-K. | **Opus** (prompt design) | **Haiku** triage → **Opus+GPT-5.5 ensemble** | **MET (shot 3 + reviewer fix 2c-F1).** Router/scorer/pre-registration sound. Review found the GPT-5.5 cross-check silently broken (9/11 responses truncated at the 700-token ceiling — reasoning tokens spend the completion budget first — plus two provider-billing outages); fixed 2026-06-12 by the reviewer: +4,000-token reasoning headroom, JSON mode when prompts demand JSON, ceiling-hit warning, regression test (`test_openai_client_adds_reasoning_headroom_and_json_mode`). **Verification slice: 12/12 working ensembles, 0 unparseable, 1 genuine `ensemble_disagreement` fired (SPB, capped at the lower probability)** — `reports/2c-F1-ensemble-slice3.json`. Standing caveat: the pre-registered validation separation (drifters 0.4725 > faders 0.4101, n=115, t≈1.55) is directional, not significant — forward paper is the proof. GPT prices in the cost table verified against real billing (2.3× correction). |
| 2d ✅ | **Entry/exit + risk/calibration wiring** (profit-take 50% @ +15%, 7% trail, −8% hard stop, 14-day time-stop) → start forward paper. | **Opus** | as 2c | **MET (shot 3)** — exit rules in `ride.py` (default OFF; ON in paper), real `LiveDataFeed` (Tiingo + SEC 8-K polling + AV refresh, persisted cursor), `harness/paper_run.py` two-lane daily tick (`proportionality` 2c-gated vs `proportionality_mech` constant-0.55 control) with sidecar state, $5/day LLM cap in code. **"Stockbot Paper Tick" scheduled daily 17:00; first run completed 2026-06-11**, both lanes snapshotted and dashboard-visible. Forward-paper clock running. |

**Phase gate:** forward-paper hit rate ≥55% directional **and** positive return net of 0.3% slippage **and** the quality bucket is calibrating.

### Hardening tickets from the shot-1 review (June 11, 2026) — gate-free, fix in shot 2

| # | Task | Build agent | Acceptance criterion |
|---|---|---|---|
| 2-R1 ✅ | **Stuck-trade edge in `bots/proportionality/ride.py` `_process_exits`.** If an entry *filled* but the position is missing at exit time (e.g. broker reconciliation surprise in live mode), the trade currently retries silently forever — the thesis never resolves and nobody is told. Add a bounded retry (e.g. 3 ticks), then record an `exit` decision with action `abandoned_position_missing` (loud in the audit trail) and drop the trade from `_open`. Theoretical today (single broker per lane); real in Phase 5. | any | **MET (shot 2)** — `MISSING_POSITION_RETRY_TICKS = 3`; after the bound, a decision row with `abandoned_position_missing` (incl. entry fill id + retry count) is recorded and the trade is dropped. The pre-existing `abandoned_no_entry_fill` path is unchanged. Proven by `tests/test_shot2_hardening.py::Test2R1StuckTradeEdge`. |
| 2-R2 ✅ | **Run-context leak on fan-out construction failure in `harness/loop.py` `drive_strategies`.** Lane construction happens before the `try/finally`; if lane *k* fails (e.g. duplicate `bot_id`), lanes 0..k−1 have started `RunContext`s that are never finished — their `runs` rows are stranded as `"running"`. Wrap construction so already-started run contexts are finished (status `failed`) when a later lane raises. | any | **MET (shot 2)** — lane construction wrapped; on failure, every already-started run context is finished with status `failed`. Proven by `tests/test_shot2_hardening.py::Test2R2RunContextLeak` (both a raising factory and the duplicate-`bot_id` path). |
| 2-R3 ✅ | **Persist `thesis_id` on fills (found during 4b-2 review, 2026-06-11).** `OrderRequest` carries `thesis_id` but `record_fill` drops it, so fills can't be joined to theses directly — dashboard v2 had to window-match by (lane, symbol, trade window), which is exact only because this strategy family holds ≤1 open trade per symbol per lane and silently breaks if that ever changes. Add the column via the existing `_MIGRATIONS` ALTER TABLE pattern, thread it through `submit_and_record`/`settle_pending_orders`, switch the dashboard drill-down to the direct join. Harness-owned — fold into the next harness-touching shot, not a dashboard ticket. | any | **MET (shot 4)** — additive `fills.thesis_id` migration (legacy rows NULL); threaded through both fill-persistence paths; dashboard drill-down uses the direct join with window-match fallback for NULL legacy rows; live paper DB migrated in place (row counts unchanged). `tests/test_shot4_r3.py` proves the two-theses-same-symbol case the window match got wrong. |
| 2c-F1 ✅ | **GPT-5.5 cross-check is silently broken (shot-3 review, 2026-06-11).** Diagnosis confirmed live on funded accounts: gpt-5.5 is a reasoning model — hidden reasoning tokens spend the `max_completion_tokens` budget before visible text, so the old 700-token ceiling truncated 9/11 responses mid-JSON (billed, HTTP 200, unparseable). Compounded by both providers running dry (OpenAI, then Anthropic) — auto-reload now recommended on both. | **Opus** (money-path correctness) | **MET (2026-06-12, fixed by reviewer).** `REASONING_HEADROOM_TOKENS=4000` + conditional JSON mode + ceiling-hit warning in `core/llm/router.py`; regression test in `tests/test_2c_llm_router.py`; suite 182 green. Verification: **12/12 ensembles, 0 unparseable, 1 real disagreement (SPB)** in `reports/2c-F1-ensemble-slice3.json`. |

---

## Phase 2.5 — Proportionality Engine, fade side (overreaction triage) 🆕

**Goal:** add the fade classification to the **same strategy class** as Phase 2 — not a new bot. The engine now answers one question per shock — *was the reaction proportionate to the fundamental dollar change?* — with three outputs: ride / **nothing (the default)** / fade. Scoped to **close-to-close gap fades** so the current daily-cadence feed suffices; intraday crash detection is a later upgrade, not a prerequisite.

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 2.5a | **Crash event detection** — flag names with daily close-to-close (or open-gap) drops ≥10% in the $300M–$10B / >$1M-ADV band; fetch the *primary source document* (8-K, prospectus, downgrade substance, court order), never the headline. | **Composer** (detection) + **Codex** (doc retrieval) | none | Detector fires on known historical crash days; correct source doc retrieved for ≥90% of a labeled sample. |
| 2.5b | **Proportionality rubric** — estimate the fundamental dollar delta (mechanical anchors where possible) vs. the market-cap change; **binary-resolution situations (trials, fraud, going-concern) auto-skip**; forced-flow context (index exclusion, fund liquidation) flagged as strongest setups. Includes the mandatory adversarial counter-pass: "argue this crash is *correct*." | **Opus** (prompt + rubric design) | **Haiku** triage → **Opus+GPT-5.5 ensemble** + counter-pass | On a labeled historical sample (post-cutoff data only, per the honesty rule), rubric separates reverting crashes from continuing ones. |
| 2.5c | **Fade entry/exit + wiring into the Phase 2 strategy class** — buy graded overreactions only; hold 2–10 days, −8% hard stop, 10-day time-stop, ≤3% position, max 2 concurrent fades; new ledger buckets (crash × fade), same calibration machinery. | **Opus** | as 2.5b | One strategy class emits both ride and fade theses; buckets separated in the ledger; forward paper starts. |

**Phase gate (the falsifier, enforced as written):** over the first 40 paper fade signals — reversion hit-rate ≥55% **and** positive net return after the cost haircut, with the crash×fade bucket calibrating. **If not, the fade side is disabled; the ride side is unaffected.**

---

## Phase 3 — Frontier Engine (formerly "Bot B: Propagation-Lag") — RESCOPED

**Goal:** trade the security an information wave hasn't reached yet — **only where materiality is certified by something other than the LLM's opinion.** Two proof-source stages. The free-form propagation graph survives as *research/candidate-generation only*; it earns trading rights bucket-by-bucket, never by default. Kill-checkpoint at 3b unchanged — a bad graph dooms everything downstream.

**Stage 1 — Customer-concentration links (pure EDGAR, no new feeds):** materiality is certified by the *disclosed* ≥20% revenue dependency.

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 3a ✅ | **Graph builder** — extract only high-magnitude typed edges: **customer-concentration (>20% rev), sole-supplier, regulatory-precedent.** Each edge carries source citation + date; edges decay. | **Codex** (big autonomous build) | **Haiku** (edge extraction at volume) | **MET (shot 2, v1)** — `stockbot/bots/frontier/` (filings → regex candidates → Haiku confirm with code-side re-validation → conservative ticker resolution → `frontier_edges` table). Ran over the 105-name universe: 100 10-Ks scanned, 386 candidate passages, 49 edges (33 concentration / 9 regulatory / 7 sole-supplier; 24 named, 25 anonymous), Haiku spend $0.24 of the $5 cap. 18-month liveness enforced by `get_live_frontier_edges`. Read-only — nothing trades on it. |
| 3b ✅ | **🚦 CHECKPOINT — graph precision spot-check.** | **Opus** (eval design) | **Opus** (LLM judge) | **PASSED ON RE-GRADE — 12/13 correct (92.3%) vs the ≥90% bar** (`reports/3b-grading-2.md`, 2026-06-11; first round failed at 54%, `reports/3b-grading.md`). The 3a-F guards verifiably eliminated all five round-1 failure patterns; recall traded for precision (24→13 named edges — U1's expanded universe regrows the graph under the stricter rules). **3c/3d are unfrozen**, with rider ticket **3a-G**: reject counterparty *categories* (plural payor/program groups like "government healthcare programs" — the single remaining miss). **3a-G MET (shot 3)** — `is_counterparty_category` guard (generic plural head nouns + government-program collectives, conservative: proper-noun phrases like "Kaiser Foundation Hospitals" untouched); graph rebuilt clean: 12 named / 24 anonymous, the AHCO category edge demoted to anonymous. Recorded caveat: n=13 is the full post-guard population; re-sample at 50 after U1 regrows the graph. |
| 3c | **Lag thesis + adversarial "already priced?" gate** — on a shock to the certified counterparty, frame the convergence thesis for the lagging name; measured **sector-neutral** vs. peers so the thesis is credited, not beta. | **Opus** (prompts) | **Opus+GPT-5.5 ensemble** (thesis) → **GPT-5.5** (priced-in check, deliberately the *other* model) | Signed theses with a working priced-in gate; sector-neutral measurement wired in. |
| 3d | **Entry/exit + wiring** (same risk/exit rules; time-stop mandatory) → start forward paper. | **Opus** | as 3c | Engine trades in paper; decisions + calibration recorded per edge-type bucket. |

**Stage 2 — ADR/polyglot lag (new feeds):** materiality is certified by the *home market's own price print*.

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 3e | **Foreign-disclosure feeds + universe mapping** — poll TDnet/DART/MOPS/DGAP during local hours; map local line ↔ US line via OpenFIGI; universe = US-listed foreign names <$5B with <3 English news items/month. | **Codex** (data-heavy) | **Haiku** (translate/classify at volume) | Disclosures land in the event stream with local-market price reaction attached; universe table populated with coverage stats. |
| 3f | **Lag measurement study (no trading)** — 60 days: does "local line moved ≥3%, US line pre-market gap < half of that" predict US open→+3d convergence? | **Codex** | none | Written answer with effect sizes. **If the US line tracks pre-market >~80% of the time, the lag is gone → shrink universe or shelve stage 2 honestly.** Long-only side only (Robinhood beta constraint). |
| 3g | *(gated on 3f)* **ADR-lag trading** through the same Frontier strategy class; new ledger buckets. | **Opus** | as 3c | Trades in forward paper; adr-lag bucket calibrating. |

**Phase gate:** sign-accuracy ≥55% in forward paper (sector-neutral) **per bucket** — concentration and ADR-lag buckets advance or die independently. If the priced-in gate rejects ~everything in a bucket, that market isn't slow → kill the bucket, keep the rest.

---

## Phase 4 — Champion/Challenger harness (race the buckets)

**Goal:** run both engines in parallel against the **same** live-paper feed. The race happens at three levels, all through the existing per-`bot_id` ledger: **engine vs engine** (Proportionality vs Frontier), **bucket vs bucket within an engine** (earnings×ride vs crash×fade; concentration vs ADR-lag) — capital allocated and killed *per bucket* — and **variant vs variant** (flow-overlay on/off, ensemble vs single model, sizing rules) as parallel instances with distinct `bot_id`s.

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 4a ✅ | **Parallel paper harness** (`harness/paper.py`) — one feed fans out to all strategy instances simultaneously; all write decisions + sim fills to the shared ledger, bot/bucket-tagged. | **Composer** | as each engine | **MET (mechanics)** — `drive_strategies` / `run_paper_fanout` race N strategies (distinct `bot_id`s enforced) on one shared feed at identical timestamps; per-lane broker/risk, shared store. Event cursor fan-out fixed (consume-once cursor is drained once per tick and pushed to every lane). `tests/test_phase4_fanout_dashboard.py` proves identical entry fills + cleanly separated ledgers. The 8-week live-paper race itself has not started. |
| 4b ✅ | **Dashboard** — equity curves, Sharpe, hit rate, drawdown, calibration-by-bucket, realized-vs-assumed slippage — sliceable **by bucket**, not just by bot; each engine also scored vs. its own benchmark (Proportionality vs. buy-hold; Frontier vs. sector-neutral). | **Composer** | none | **MET (v1, minimal)** — `stockbot/dashboard/` (stdlib HTTP, zero new deps): equity curves per bot/mode, calibration by `strategy_bucket` with the capital-gate flag, realized-vs-assumed slippage. `python -m stockbot.dashboard.app`. Benchmark overlays = later. |

| 4b-2 | **Dashboard v2 (user-requested, build after shot 3 lands).** The watching-the-race view: (1) **ops health strip** — did each scheduled job run today (green/red), last paper-tick time, daily LLM/API spend; (2) **open positions table** per lane with live P&L, entry thesis link, exit-rule state (distance to trail/stop/time-stop); (3) **thesis browser** — every trade's LLM rationale, quality score, ensemble flags (disagreement/degraded), drill-down from any fill; (4) **race view** — gated vs `proportionality_mech` control head-to-head, cumulative + per-event; (5) **calibration curves** — stated probability vs realized hit rate per bucket, not just the gate flag; (6) crumbs alert feed + Flow Lens consensus/novelty board; (7) auto-refresh + a logon scheduled task so it's always up at `127.0.0.1:8765`. Keep stdlib or move to FastAPI+htmx — builder's call; read-only against the DB either way. | **Composer** (display plumbing) | none | All seven panels render from the live paper DB; zero new write paths to the state store. |

**Phase gate:** ≥8 weeks of parallel paper with **at least one bucket** stable, positive, and calibrated, net of honest slippage. Anything not calibrated does **not** advance — buckets advance or die independently, enforced as written. (Zero buckets advancing is a valid outcome → keep iterating in paper.)

---

## Phase 5 — Go live (built-in from day one, because the spine never changed)

**Goal:** flip the winners to real money with minimal new code.

| # | Task | Build agent | Runtime | Acceptance criterion |
|---|---|---|---|---|
| 5a ✅ | **Live `Broker` implementation** — Robinhood MCP (`https://agent.robinhood.com/mcp/trading`) primary + **Alpaca** hot spare; both behind the existing `Broker` interface and using 0.1b reconciliation. | **Opus** (money-critical) + **Composer** (client plumbing) | none | **MET (shot 4, adapted to the $0-account reality)** — `RobinhoodMCPBroker` (MCP-over-HTTP + OAuth/PKCE w/ dynamic client registration; auth resolved, browser consent = one user action) + `AlpacaBroker`; rails in the brokers themselves (account pin, mode pin, $50 ceiling, manual-approval queue + `approve_orders` CLI, KILL_SWITCH); Robinhood read path + `review_equity_order` round-trip verified live ($0 account, zero orders); Alpaca full lifecycle green against the real paper API. Offline fixture tests for both. |
| 5b ✅ | **Shadow / dry-run** (1–2 wks) — live data + live decisions, but log would-be orders and place *none*. | **Opus** | as each bot | **BUILT (shot 4; the ~1-week shadow clock now runs)** — third lane `proportionality_shadow` on the same daily 17:00 tick (one process, shared gate cache = no extra LLM spend), orders logged as `shadow_order` decision rows; shadow contexts physically cannot wire to a live broker (ctor guard + test). Exit criteria reviewer-judged after ~1 week of trading days. |
| 5c | **Go live at $50/trade** — manual-approval mode ON, kill switch, daily-loss halt (−3% → no new entries), drawdown halt (−8% → stop), per-order rate limit. | **Opus** | as each bot | First real fills; **realized slippage tracked vs. paper assumption** (if real >2× paper → shrink to more liquid names). |

**Setup:** open a **dedicated Robinhood agentic account per engine** (funds isolated, one agent per account) — two accounts initially; a third only if the Flow strategy ever graduates from Workstream F. Fund tiny ($500–$1k each). Within an account, only **calibrated buckets** receive allocation.
**Scale rule:** account size doubles only after **60 days of positive *realized* P&L** at each tier. No exceptions, no "it feels ready."

### 🎯 Live pilot — decided 2026-06-11 (user choice, protocol pre-committed before any fills)

The user elected to run a **$500 live pilot in parallel with the Phase 4 paper race** rather than after it. Reframed and locked as follows — the pilot is a **slippage-measurement experiment, not an edge verdict**:

1. **Sequencing:** 5a + 5b move up to become **shot 4** (immediately after shot 3 clears review); Phase 2.5 (fade side) slides one slot. Shadow mode compresses to ~1 week. Pilot = 5c at $500, earliest ~2–3 weeks from 2026-06-11.
2. **Hard caps:** $500 in, never topped up mid-pilot; $50/trade; manual-approval ON for at least the first 10 orders; all existing halts and the kill switch apply.
3. **The pre-commitment that makes this safe:** the pilot's P&L gates NOTHING. Scaling decisions remain exactly as written above — calibrated bucket + paper-race gate + 60-day doubling rule. A lucky pilot does not accelerate capital; an unlucky one does not kill a bucket that paper says is healthy.
4. **What the pilot is FOR:** realized-vs-modeled slippage per fill (the biggest unknown in the 2b result), broker-integration verification, operational bugs. Success = clean fill data, not green P&L. If realized slippage > 2× modeled → shrink to more liquid names or pause, per 5c.
5. **User action now:** start Robinhood agentic-account onboarding immediately — beta approval is the long pole on any live timeline.

---

## Sequencing summary

| Phase | Output | Gate to advance |
|---|---|---|
| 0 ✅ | Three-mode spine | **DONE** — 9/9 tests pass, config-only switch verified |
| 0.1 ✅ | Hardened foundation | **DONE** — persistence, reconciliation, active PIT, audit log, run metadata all green |
| 1 ✅ | Historical backtest harness | **DONE (mechanics)** — 1a/1b/1c on synthetic CSV data; proves plumbing, not edge |
| 1.5 ✅ | Strategy abstraction + real data | **DONE** — 1.5a–i complete (including real earnings-surprise signal and risk re-enforcement) |
| F 🆕 | Flow Lens + Crumbs (parallel, start now) | F1 logger **done** (start daily `schedule` for 4+ wk accumulation); F2 study answers H1; F3 overlay gated on F2; F4 catches known tenders |
| 2 | Proportionality Engine — ride side (= old Bot A) | 2b drift checkpoint → forward-paper ≥55% hit, +EV net slippage, calibrating |
| 2.5 🆕 | Proportionality Engine — fade side | 40-signal falsifier: ≥55% reversion hit + net +EV, crash×fade bucket calibrating — else fade side disabled, ride side unaffected |
| 3 | Frontier Engine (= old Bot B, rescoped) | 3b graph-precision checkpoint → per-bucket ≥55% sector-neutral sign-accuracy; concentration & ADR-lag buckets advance or die independently |
| 4 | Champion/challenger paper | 8+ wks parallel; ≥1 **bucket** calibrated & +EV |
| 5 | Live (one agentic account per engine) | dry-run clean → $50/trade → double only after 60d realized +P&L |

**Realistic timeline** (part-time, agents doing the heavy lifting): 1.5h ~1 week; Workstream F1/F4 ~2–3 days and then runs itself; ride side ~2 weeks; fade side ~1–2 weeks (shares the engine's plumbing); Frontier stage 1 ~3 weeks, stage 2 after its 60-day measurement; then paper-racing for a couple months *while you watch*. Live money realistically ~3–4 months out — the paper-racing months are exactly when you find out which buckets are real before they can cost you.

---

## What to build next, concretely

**Status as of June 11, 2026 (evening): 2a ✅, 2b ✅ GATE PASSED, 4a/4b ✅. Full suite: 74 passed.** Shot 1 passed review (numbers reproduce bit-identically, surprise arithmetic hand-verified, all hard rules honored). A premium AV key collapsed the cache wait: the full 100-name universe ran the same day and the 2b gate **passed against the pre-committed criteria** (see the 2b row). **2c/2d are unblocked.**

**Shot 3 delivered and REVIEWED (`reports/shot3-report.md`; full suite 181 passed, re-run by reviewer):** 3a-F guards ✅ (re-grade PASSED 92.3%, 3c/3d unfrozen), rider 3a-G ✅, U1 mechanical universe ✅ (1,376 names; expanded drift secondary 842 entries / 61.98% hit / **+5.80% mean, every year positive** — the edge *strengthens* on the mechanically screened universe; ignore the 19.45 Sharpe = cash drag), 2d forward paper ✅ (two lanes live, daily 17:00 task, first run completed clean, 0 fills = correct next-bar behavior). 2c flagged ⚠️ at review, then **fixed and closed same night (2c-F1 ✅: reasoning-token headroom + JSON mode; verification 12/12 ensembles, 1 real disagreement; suite 182).** Hard stop honored: no 2.5, no 3c/3d code, no Phase 5. **Phase 2 forward-paper clock is running with the full two-model ensemble.**

**Shot 4 delivered (`reports/shot4-report.md`; suite 182 → 212 passed, zero real orders):** 2-R3 ✅ (thesis_id on fills, live DB migrated in place), 2c-F2 ✅ (reconcile script + Opus row recalibrated from the clean Anthropic delta — implied real ≈ $5/$25 per MTok, set (6, 30) per the high-never-low rule; gpt-5.5 row waits on the settled June OpenAI reading), 5a ✅ (RobinhoodMCPBroker: OAuth/PKCE auth RESOLVED — discovery + dynamic client registration open, `rh_auth login` is the one remaining user action; read path + review round-trip verified live on the $0 agentic account; AlpacaBroker full lifecycle green on the real paper API; $50 ceiling/kill switch/approval CLI built and tested), 5b ✅ built (shadow lane registered on the daily 17:00 tick; **shadow week clock starts with tonight's tick**). After shadow week passes review: user funds $500 → pilot starts under the pre-committed protocol. **User actions now: run `python -m stockbot.tools.rh_auth login` once (records token lifetime in the reference doc); supply the settled June OpenAI dashboard reading for the gpt-5.5 row.**

**Shot-4 REVIEW: PASS (2026-06-12, reviewer; suite 212 re-run, migration verified on the live DB, rails verified in code).** Two notes on record: (1) the report said the gpt-5.5 price row was "untouched" but the code shows it padded (5,30)→(7,40) — safe direction (estimates high, caps bind early), substance fine, report wording imprecise; final value still awaits the settled OpenAI reading. (2) `pending_approval_orders`/`broker_order_refs` are absent from paper.db (correct — they belong to the live DB); verify they materialize at first live-broker use, checked at shadow-week review. **Rulings:** shadow simulated-bookkeeping BLESSED (the decision-match exit criterion requires it; the ctor-level live-broker guard is the right enforcement point); shadow lane sharing `earnings:ride` bucket separated by bot_id confirmed correct; weekly `rh_auth status` ops check approved (shot-5 rider); `data/shadow_demo/` may be deleted. **Shadow-week exit (reviewer judges after ~5 trading days): shadow decisions match the gated lane event-for-event; zero missed ticks / auth expiries / feed gaps.**

**Shot 5 delivered (`reports/shot5-report.md`; suite 212 → 243, zero real orders) and REVIEWED (2026-06-12, reviewer; suite re-run 243, then 254 with the 5-R1 fix).** Ticket verdicts: R1 ✅ (auth task registered + verified, shadow_demo deleted; gpt-5.5 row correctly skipped — still owed the settled reading), 2.5a ✅ (constants pre-registered in code, 14/14 retrieved docs causal vs ≥90% bar, the one no-doc case verified honest; band caveat honestly documented), 2.5b ✅ **with eyes open** (pre-registration discipline held — nothing tuned after results; separation directional but tiny (+0.008), "overreaction" class reverted LESS than base rate on the cap-tripped head sample, 13 would-fire entries had −0.84% mean. Accepted because the criterion was met as written and the race-time 40-signal falsifier is the real test; the fade side must EARN its keep forward or be disabled with the ride side unaffected), 2.5c ✅ (fade-disabled-by-default bit-identical, exits pre-registered, counter-pass logged on every fade decision row, scratch tick showed brain-vs-coin split live: brain 0 fades incl. one counter-pass veto, coin entered 2). **3c-prep: sample regenerated correctly (seed 3003, ungraded) — but the reviewer grade FAILED: 32/50 = 64% vs ≥90% (`reports/3b-grading-3.md`; 68% under the most lenient reading — verdict robust). 3c/3d STAY FROZEN.** The expanded universe defeated the round-2 guards (internal segments/plants/verticals, geographies, non-entity counterparties, anonymous-in-named leaks) and exposed three NEW failure classes needing guards: relationship currency (terminated/historical language must veto), magnitude denominator (segment/subsidiary revenue claimed as company revenue), latest-year selection. Re-test seed **3004** pre-registered in the grading report. The graph is salvageable by a guard re-filter pass (quotes retained on every edge), not a rebuild.

**Reviewer fix 5-R1 (2026-06-12, shipped same night — first weekend of the live race was the next day):** the system did not handle non-trading days. The tick tasks fire daily incl. weekends, and on a non-trading day Tiingo IEX still serves the LAST session — so (a) Friday's next-bar orders would settle Saturday at Friday's own price, deleting the overnight gap next-bar execution exists to capture, and (b) Friday's ≥10% crashes would be re-detected Sat AND Sun under fresh event_ids: fresh LLM spend each day, and a counter-pass-vetoed fade gets a fresh roll of the dice — a systematic bias toward entry never present in validation. Fix: `stockbot/harness/trading_calendar.py` (weekends + 2026/27 NYSE holidays, fail-OPEN beyond the table), `paper_run.main` skips non-trading days (`--force` override), and `LiveCrashDetector` drops rows whose session timestamp isn't today's (also kills re-detection of crashed-then-HALTED names on weekdays). 11 tests in `tests/test_shot5_review_weekend.py`; suite 254. Note: **Juneteenth (Fri 2026-06-19) is a market holiday inside the shadow week** — with the fix the tick correctly skips it, so the shadow week's ~5 trading days run Jun 15–18 + Jun 22; review lands ~Jun 22/23.

**Shot 6 delivered (two parallel builder agents after Fable was cut; suite 254 → 280, zero real orders) and REVIEWED (2026-06-13, reviewer; suite re-run 280, dashboard verified live at :8765).** Ticket verdicts: **3a-H ✅** (11/11 guard fixtures green — all 18 round-3 failures killed/demoted by their pre-registered guard, 31/32 correct retained; the one precedence fix (G1 vs G5 on dual-failure edge #7) was made by reordering the stack, fixtures/EXPECTED_* untouched). **3c-prep-2 ✅ mechanically** (full guard stack applied to 373 edges → 314 survive / 138 named, $0 LLM, deterministic; honest collateral + dedupe disclosure). **4b-3 ✅** (dashboard readability layer live: summary strip, per-panel "what am I looking at?" explainers, deterministic verdict templates, flag/column tooltips, `/glossary` page, phase-journey strip — verified rendering at :8765; Agent A shipped the code + tests but left its report section blank — reporting gap, not a work gap). **R1** closed by reviewer as before.

**3c-prep-2 precision grade: seed-3004 sample FAILED at 44/50 = 88% (`reports/3b-grading-4.md`), one edge under the ≥90% bar. 3c/3d STAY FROZEN.** Big jump from round 3 (64% → 88%) — the guards did real work — but **four of six failures are MISSES of classes the guards already target** (stale-year AMBQ/Huawei, direction-reversal AQST/Pharmanovia [same company as round-3 #2], internal-segment GNE/GRE, non-entity EXPO/industry): the regex patterns don't generalize to new surface forms. Two are new semantic classes a regex fundamentally can't judge (product-as-supplier AUPH/LUPKYNIS; hypothetical-vs-actual regulatory language ZURA/FDA). **Reviewer's call: pattern-guards have hit their precision ceiling (~high-80s); more regex rounds will play whack-a-mole.** Recommended shot-7 change: keep the regex guards as a free pre-filter, add a **cheap Haiku edge-validator** (one call per candidate edge, ~$0.05–0.15 over 138 names, frozen rubric/temp 0) as the precision layer targeting the semantic failures, then a fresh held-out **seed-3005** sample graded by the reviewer. Secondary: insert-time dedupe leaks duplicate edges (EVTC Popular/Banco Popular, GLIBA/GLIBK, CENT/Home Depot) into the pool — fix before edge counts gate sizing.

**Shot 7 delivered (two parallel agents; suite 280 → 302, zero real orders) and REVIEWED (2026-06-13, reviewer; suite re-run 302, pipeline output verified).** Belt-and-suspenders edge precision: Agent A sharpened the regex guards (new G8 product-as-counterparty, G9 hypothetical-regulatory, extended G1/G2/G6/G7) with the round-3+round-4 graded edges as frozen fixtures; Agent B built the Haiku semantic validator (frozen rubric, 5 checks, fail-closed, cached, temp 0), insert-time dedupe, and the combined 3-stage pipeline (regex → dedupe → validator). Combined re-filter: 314 → 275 edges / 138 → 106 named ($0.21 validator spend, token-logged). Validator dev-check on my round-4 labels: 6/6 recall on the failures, 35/44 specificity (conservative — over-kills borderlines, which *helps* a precision bar). One disclosed v1→v2 rubric revision (v1 too strict), re-frozen, no peek at the held-out survivors.

**3c-prep-2 precision grade: seed-3005 sample PASSED at 48/50 = 96% (`reports/3b-grading-5.md`). 3c/3d UNFROZEN.** Trajectory 64% → 88% → **96%**. Every regulatory_precedent edge now describes an actual dated settlement (the hypothetical-boilerplate class is gone). The two misses: AIN/Safran (segment-denominator "37% of AEC's net revenues" slipped check-4) and MBUU/GM (soft "sole or limited" supplier language — softest call; lenient reading = 98%). Integrity verified: seed 3005 was pre-registered, both failures were fresh (not in the round-4 labeled set, so no overfit), spend honest, suite independently re-run. Two non-blocking carry-forwards for shot 8: tighten check-4 for "of {segment}'s revenue", and a dedupe that tolerates a 1-char counterparty typo (an AQST/Indivior dup survived).

**Shot 8 delivered (one agent, end-to-end; suite 280 → 315, zero real orders) and REVIEWED (2026-06-13, reviewer; suite re-run 315, constants/backtest/config independently verified).** Frontier propagation engine BUILT and MEASURED honestly. Verified: pre-registered constants frozen in code match the report (anchor shock 5% down-only, hold 5d, PIT 548d, direction map down/down, no LLM); backtest JSON matches; frontier lanes default OFF (live race bit-identical); regulatory_precedent correctly out of scope (can't price-anchor on "the EPA"). **Result is WEAK and does NOT clear the gate — honestly so:** only **22 tradeable edges** (resolved counterparty ticker; ~70–83% of named edges have NULL counterparty), **21 lifetime signals, 19 resolved**. Per-bucket sector-neutral sign-accuracy: customer_concentration **50.0% (n=12)** — coin flip; sole_supplier **57.1% (n=7)** — on-thesis (negative mean excess) but n=7 is noise. Neither convincingly clears ≥55%. **Design limitation surfaced:** every prediction is DOWN, and the long-only cash stack can't short — so the engine records/resolves directional theses (sign-accuracy) but cannot monetize on Robinhood as built; biggest lever to fix thinness is counterparty-ticker resolution (the NULL wall), deferred not silently applied. **Disposition: Frontier built, NOT proven; 3e/3f stay deferred; turn the lanes ON to accumulate the forward sign-accuracy sample (mechanical, no capital risk).**

**Shot 9 delivered (one agent; suite 315 → 338, zero real orders) and REVIEWED: PASS (2026-06-13, reviewer; suite re-run 338, live read path re-run independently, config/guard verified).** Go-live readiness complete. **9a** — the `data`-envelope blocker FIXED (`MCPHTTPClient._unwrap_data_envelope`, centralized in `call_tool`, `keys ⊆ {data,guide}` guard so genuine payloads aren't mis-stripped; all 7 consumers audited incl. order placement); 13 real-shape regression tests. **9b** — live read-path VERIFIED on ••••0804 (reviewer re-ran `scripts/rh_readcheck.py`: account guard passes, $0 buying power, 0 positions — the proof shot 4 claimed but lacked; before 9a the guard crashed on the real envelope). **9c** — `docs/go-live-runbook.md` + `scripts/approval_dryrun.py` exercised the manual-approval queue end-to-end with no venue order (`pending_approval_orders`/`broker_order_refs` materialize, approve→placed/acked, reject→rejected). **9d** — frontier lanes ON (`frontier.enabled: true`). **9e** — UP-propagation added (additive, pre-registered separate constants/buckets, long-tradeable BUY; down side bit-identical — reproduces shot-8 exactly; up side fires MORE: 34 vs 21 signals, customer_up 42.9% n=14, sole_supplier_up 68.75% n=16 — thin/encouraging, not significant). Verified: account guard still REFUSES ••••2954 (raises on `agentic_allowed`); `broker.live` still `simulated`; $0 funded; no rail weakened. **The $500 pilot is now a flip-and-approve following the runbook.**

**Next: a deliberate WAIT, not a build.** The machinery has caught up to the calendar. Remaining gates to the pilot are time + judgment, not code: (1) the **shadow week** accumulates over Jun 15–18 + 22 trading days (Jun 19 Juneteenth skipped); (2) **reviewer judges shadow ~Jun 22/23** (shadow decisions match the gated lane event-for-event, zero ticks/auth/feed failures; live approval tables now proven via 9c); (3) **user funds $500** → pilot under the pre-committed protocol. In parallel, fade + frontier lanes accumulate forward samples (40-signal fade falsifier; per-bucket frontier sign-accuracy). July earnings season is when ride/fade samples get real; F2 flow study needs ~4 wks accumulation. Deferred/low-priority: shot-7 validator/dedupe precision carry-forwards (twice deferred — a tidy-up, non-gating). Future, Fable-dependent: Frontier monthly economic-links reformulation, options/shorting branches (see [[frontier-and-shorting-strategy]]).

**Shot 2 delivered and reviewed (`reports/shot2-report.md`; full suite 99 passed):** hardening 2-R1/2-R2 ✅, ops automation ✅ (three scheduled tasks live), 3a graph builder ✅ built honestly — but the **3b checkpoint FAILED the precision grade: 54% vs the ≥90% bar** (`reports/3b-grading.md`). Five mechanical failure patterns identified, all guard-fixable (ticket 3a-F). 3c/3d stay frozen until a re-graded sample (new seed 3002) passes. **The 3b fail does not block Phase 2** — shot 3 proceeds: **3a-F + U1 + 2c + 2d (forward paper starts)**, brief in `handoff-shot3.md`. **User actions on the critical path: fund the OpenAI account (2c's GPT ensemble cross-check + Flow Lens currently 429 insufficient_quota); Tiingo paid tier ✅ done.**

**Ticket U1 — mechanical universe expansion (bundle into shot 3, while the premium AV key is live).** The 100-name universe produces ~44 top-decile entries/year ≈ <1 signal/week — far too thin for an 8-week forward-paper race to reach a meaningful sample. Expand to every US-listed name passing a *mechanical* screen (no hand-curation): $300M–$3B market cap (AV `OVERVIEW` endpoint, 1 req/name — premium key makes this an afternoon), median dollar-ADV ≥ $500k computed from prices, primary exchanges only. Target ~500–1,000 names → ~5–10 paper signals/week. Survivorship caveat shrinks (screen is mechanical, run today) but does not vanish (delisted names still absent); document as before. **Watch the Tiingo free-tier unique-symbol cap (~500/month) — a one-month paid Tiingo tier (~$10–30) may be needed alongside the AV premium month; both cancel after.** Acceptance: new universe CSV generated by the screener script (reproducible), full cache + dataset built, drift study re-run on the expanded universe as a *declared secondary* (the 2b gate stays decided on the pre-registered run).

**Conventions for whoever builds Phase 2** (so the schema is used as intended):
- Tag every thesis + calibration row with `strategy_bucket=CalibrationLedger.strategy_bucket(event_type, direction)` — e.g. `"earnings:ride"`.
- Gate real-capital sizing on `is_strategy_bucket_calibrated(bot_id, bucket)` — paper trades regardless; money only for calibrated buckets.
- `bot_id` = engine name (`proportionality`, `frontier`); the engine×bucket pair is the unit Phase 4 races and Phase 5 funds.

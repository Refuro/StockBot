# Shot 2 Handoff — Hardening + Ops automation + Phase 3 Stage 1 (Frontier graph builder)

You are the end-to-end build agent for this repo. This is your brief for **shot 2 only**. A reviewing agent audits your work at the end; build honestly, not to make a gate pass. Shot 1 (`handoff-shot1.md` → `reports/shot1-report.md`) passed review — read both to see the bar.

## Required reading (in order, before any code)
1. [implementation-plan.md](implementation-plan.md) — Phase 3 Stage 1, the "Hardening tickets from the shot-1 review" table, and the conventions block at the bottom
2. [strategy-synthesis.md](strategy-synthesis.md) — especially the Frontier Engine's *externally-certified materiality* rule
3. [reports/shot1-report.md](reports/shot1-report.md) — what a passing completion report looks like

## Environment facts
- Windows 11, venv at `.venv` — run tests with `.\.venv\Scripts\python.exe -m pytest tests/ -q`
- Current suite: **74 passed.** It must stay green. Do not modify existing tests to make new code pass.
- Keys live in `.env`: Tiingo, Alpha Vantage, SEC_USER_AGENT, **ANTHROPIC_API_KEY, OPENAI_API_KEY** (verified set). Never print key values.
- Alpha Vantage budget: 25 requests/day, already managed by `stockbot/data/ingest_universe.py` (tombstones + budget cap). Do not spend AV requests outside that path.

## Context you must not violate

**The 2b gate is pending, not passed.** The reviewer pre-committed the criteria (see the 2b row in the plan): the call happens at full AV cache coverage, PASS = primary cell holds positive mean net return AND >50% hit. Until that call:
- Do **NOT** build 2c (LLM quality scoring) or 2d (entry/exit wiring).
- Do **NOT** change anything in `stockbot/research/drift_study.py` — no new cells, no parameter changes, no "improvements." Re-running it mechanically is fine (it's how the report refreshes as coverage grows).

## Scope — build ONLY this, in this order

### Ticket H — the two shot-1 hardening fixes (2-R1, 2-R2 in the plan)
Small, surgical, each with a test. Exact specs and acceptance criteria are in the plan's "Hardening tickets from the shot-1 review" table. Do these first — they touch files 3a will build on.

### Ticket OPS — scheduled tasks so the data pipeline runs itself
Register **Windows Task Scheduler** jobs (use `Register-ScheduledTask` or `schtasks`, working directory = repo root, absolute path to `.venv\Scripts\python.exe`, each appending stdout/stderr to a dated file under `logs/`):

1. **Daily ingestion + study refresh** (e.g. 08:00 local): `python -m stockbot.data.ingest_universe --start 2022-06-01 --end 2026-06-09` followed by `python -m stockbot.research.drift_study` (only when ingestion exits 0). This is what fills the AV cache ~25 names/day toward the 2b gate call.
2. **Daily crumbs scan** (e.g. 08:30): `python -m stockbot.tools.crumbs.scanner` — needs only SEC_USER_AGENT.
3. **Daily Flow Lens run** (e.g. 09:00): the F1 accumulation run, using **only providers whose keys exist** (OpenAI + Anthropic today; skip Grok/Gemini cleanly, log which providers ran). Cost note: this spends real API money daily (small, ~$1–3); keep per-run spend visible in the log line.

Acceptance: each job runs once manually end-to-end before you register it; `Get-ScheduledTask` shows all three; logs land in `logs/`. Document the exact task names + how to pause/remove them in your report.

### Ticket 3a — Frontier Engine Stage 1: typed-edge graph builder (the main course)

**What it is** (plan, Phase 3 Stage 1): extract **high-magnitude typed edges** from SEC filings — `customer_concentration` (disclosed ≥20% of revenue), `sole_supplier`, `regulatory_precedent` — into a graph the engine can later trade on. Materiality must be **externally certified by the disclosure itself**, never by an LLM's opinion.

**Scope guardrails:**
- **Universe:** the existing 105-name candidate list (`stockbot/data/universe_shot1.csv`). Design the pipeline so the universe is a parameter (it will grow later), but do not expand it this shot.
- **Source:** EDGAR 10-K (and 10-K/A) filings, most recent per company, via the SEC submissions/archives endpoints you already use (`SEC_USER_AGENT`, polite rate limiting, cached via `CachedHTTP`). 10-Qs optional if cheap.
- **Extraction = regex-first, LLM-confirm:** mechanically locate candidate passages (revenue-concentration language, percentages, "sole supplier", named customers), then use **Haiku (`claude-haiku-4-5-20251001`)** only to (a) confirm the passage actually asserts the edge, (b) type it, and (c) extract the named counterparty + percentage. The LLM never invents an edge without a quoted passage. **Hard spend cap for the whole shot: $5; batch requests; log token spend.**
- **Every edge row carries:** edge type, from-symbol, counterparty (name + resolved ticker where possible), magnitude (e.g. revenue %), the **quoted sentence(s)**, accession number, filing URL, `filed_at`, and `extracted_at`. Counterparty→ticker resolution uses the SEC `company_tickers.json` map, **conservative exact-ish matching only** — an unresolved name stays as a name (do not fuzzy-guess tickers).
- **Anonymous counterparties** ("one customer accounted for 27% of revenue") are recorded with a `named=False` flag for stats, but only **named** edges count as graph edges. Expect many anonymous ones; report the ratio.
- **Decay:** store `filed_at`; define edges as *live* only if the source filing is ≤18 months old. A query helper enforces this; no background process needed.
- **Storage:** new table(s) in `StateStore` via `CREATE TABLE IF NOT EXISTS` (the existing pattern). Read paths only for now — nothing trades on this graph in shot 2.
- **NO trading code.** No `Strategy`, no theses, no orders. 3c/3d are a later shot, gated on 3b.

**Output for the 3b checkpoint (you do not judge it):**
- `reports/3b-precision-sample.md` — a **random sample of 50 named edges** (or all, if fewer), each showing: claimed edge (type, from→to, magnitude), the quoted passage, and the filing URL, formatted so a reviewer can verify each in under a minute.
- Pre-registered 3b bar (set by the reviewer, 2026-06-11): **≥90% of sampled edges must be correct** — the quote genuinely asserts the typed relationship at the claimed magnitude AND the ticker resolution (when present) is right. You build the sample; the reviewer grades it.

## Hard rules
1. **STOP after 3a.** No 3c/3d, no 2c/2d. Both checkpoints (2b verdict, 3b grading) belong to the reviewer.
2. Do not touch: `RiskEngine`, the PIT guards, `drift_study.py` cell definitions, existing tests, `strategy-*.md` docs.
3. LLM calls in this shot are **extraction-only** (Haiku, capped at $5). No LLM touches a trading decision anywhere.
4. SEC scraping: honor the user agent + rate limits; everything through `CachedHTTP` so re-runs are free and tests run offline from fixtures.
5. New code gets tests (offline fixtures for extraction — golden filings excerpts in `fixtures/`). Suite stays green. If genuinely blocked, document it in the report and stop — never improvise around a gate or a data problem.

## Completion report (required, as `reports/shot2-report.md`)
- Files added/changed and why (one line each)
- Test count before/after; full-suite output tail
- H tickets: what changed, which tests prove them
- OPS: task names, schedules, log locations, how to pause/remove; one successful manual run of each shown
- 3a stats: filings scanned, candidate passages, edges by type, named-vs-anonymous ratio, ticker-resolution rate, total Haiku spend
- Confirmation that `reports/3b-precision-sample.md` exists and how the random sample was drawn (seed it for reproducibility)
- Any deviations from this brief, with reasons
- Open questions for the reviewer

# Shot 4 Handoff — 2-R3 + 2c-F2 + Phase 5a (live brokers) + 5b (shadow mode)

You are the end-to-end build agent. This brief covers **shot 4 only**. The reviewer audits at the end. Shots 1–3 set the bar (`reports/shot1-report.md` … `shot3-report.md`).

## Required reading (before any code)
1. [implementation-plan.md](implementation-plan.md) — Phase 5 incl. the **Live pilot protocol** block (pre-committed; do not weaken it), tickets 2-R3 / 2c-F1 (closed, for context), conventions block
2. [docs/robinhood-mcp-reference.md](docs/robinhood-mcp-reference.md) — **ground truth for 5a**, captured from the live authenticated endpoint: accounts, tool semantics, order workflow, idempotency, the auth question
3. `reports/spend-reconciliation-test.json` + the 2c-F1 slice files — context for 2c-F2

## Environment facts
- Suite: **182 passed** — stays green; never modify existing tests.
- Paper race is LIVE (daily 17:00 task, two lanes). **Do not disturb it**: no changes to `paper_run.py` semantics, the ride strategy's behavior, or `data/paper.db`. The 2-R3 schema migration is the one sanctioned touch (additive column).
- Robinhood agentic account: number ends **0804**, `agentic_allowed=true`, cash type, **$0 balance — and it stays $0 this shot.** The user's default account (ends 2954) is `agentic_allowed=false`; Robinhood rejects trading calls against it server-side. Hard-pin 0804 in config anyway.
- Alpaca: keys in `.env`; add `ALPACA_BASE_URL=https://paper-api.alpaca.markets` to `.env`/`.env.example` (paper host now; live host is a Phase-5c config change).
- Keys all funded; Anthropic auto-reload on. OpenAI dashboard lags spend by hours — relevant to 2c-F2.

## Scope — build ONLY this, in this order

### Ticket 2-R3 — persist `thesis_id` on fills (small, first)
Spec is in the plan's hardening table. Additive `_MIGRATIONS` column, thread through `submit_and_record`/`settle_pending_orders`, switch the dashboard drill-down to the direct join. Existing DBs must migrate in place; suite green.

### Ticket 2c-F2 — LLM price-constant recalibration (small)
Tonight's controlled test (`reports/spend-reconciliation-test.json`) vs real dashboards showed our estimates run ~2.2–2.7× HIGH (safe direction, still wrong): tracker logged $0.83 GPT / $2.20 Anthropic against real deltas of ~$0.37 (OpenAI, **incomplete — dashboard lag**) and $0.81 (Anthropic, clean).
- Build `scripts/reconcile_llm_spend.py`: reads a run's spend JSON + prompts for the two dashboard deltas → prints estimated-vs-real per provider and the implied real $/MTok. This becomes the monthly ops habit.
- Recalibrate `PRICE_PER_MTOK` from **clean** measurements only: the Anthropic delta above is usable now; for OpenAI, ask the user for the settled June-spend reading (hours after the test) before touching the gpt-5.5 row — do NOT recalibrate from the lag-truncated $0.37. The shot-3 "2.3× correction" was itself contaminated (failed/truncated-call era) and will likely partially revert.
- Acceptance: reconcile script + updated constants with the measurement cited in a comment + the standing rule documented: **estimates may run high (caps bite early — safe), never knowingly low.**

### Ticket 5a — live `Broker` implementations (the main course)
Two brokers behind the EXISTING `Broker` interface (`core/broker/base.py`), wired through the broker factory by config. No strategy code changes — that's the whole point of the spine.

**`RobinhoodMCPBroker`** (`core/broker/robinhood_mcp.py`):
- Speaks MCP-over-HTTP (JSON-RPC) directly to `https://agent.robinhood.com/mcp/trading` via httpx — no AI client involved. Tool names/semantics per the reference doc.
- **Auth (the one true unknown — resolve FIRST, timebox it):** OAuth2 + PKCE against `robinhood.com/oauth` (S256, localhost callback, `resource` bound to the MCP URL — parameters visible in the reference doc). Implement: one-time interactive `python -m stockbot.tools.rh_auth login` (opens browser, captures callback, stores tokens in `secrets/robinhood_token.json`, chmod-equivalent restricted), automatic refresh on expiry, clear re-auth error when refresh dies. Inspect the token response and DOCUMENT lifetime/refresh semantics in the reference doc. If the OAuth client registration turns out to be closed (the `client_id` in the doc is Claude's), try MCP dynamic client registration per the MCP auth spec; if that's also closed, STOP, document, and flag the reviewer — do not scrape or spoof a client id.
- Mapping per the reference doc table: `submit_order` → `review_equity_order` then `place_equity_order` with a **client-generated `ref_id` (UUID) persisted to the state DB before the call** (crash-safe idempotency); portfolio/positions/orders/cancel per the doc; positions reconcile from the venue (0.1b pattern), never from memory.
- **Safety rails in the broker itself** (not only the risk engine): refuse any account that isn't the configured agentic account; refuse if `mode != live`; per-order notional ceiling from config (default $50); **manual-approval hook** — when `live_manual_approval: true`, write the proposed order as a `pending_approval` row + log line instead of placing; a tiny CLI (`python -m stockbot.tools.approve_orders`) lists and approves/rejects. This is the mechanism the pilot's first-10-orders rule uses.
- **Kill switch:** `KILL_SWITCH` file at repo root (and/or config flag) — if present, the broker rejects ALL new orders (exits via approval CLI still allowed). Document the one-liner to engage it.

**`AlpacaBroker`** (`core/broker/alpaca.py`): same interface, REST against `ALPACA_BASE_URL` (paper). Plain key auth. Same rails (notional ceiling, kill switch). This one CAN be order-tested end-to-end against the paper API — do so.

**Acceptance (5a), adapted to the $0-account reality:**
- Robinhood: auth + token refresh proven live; `get_accounts`/`get_portfolio`/`get_equity_positions`/`get_equity_quotes` read real data; `review_equity_order` round-trips a simulated $50 order on the agentic account (review is simulation — places nothing); `place_equity_order` is NOT called this shot (account is $0; the first real call happens at pilot start under manual approval).
- Alpaca: full order lifecycle (place tiny paper order → poll fill → position reconcile → cancel path) green against paper API.
- Unit tests with recorded/stubbed HTTP fixtures for both brokers (offline, deterministic); suite green.

### Ticket 5b — shadow mode (runs ~1 week after this shot lands)
- New mode of the daily tick (config: `shadow: true` on a third lane or a parallel run against the live-feed): live data, live 2c decisions, **orders logged as `shadow_order` decision rows — broker never called.** Reuse the paper lanes' brain; tag `bot_id="proportionality_shadow"`.
- Register it into the daily schedule alongside the paper tick (same 17:00 run, additional lane — keep ONE process so feed/cursor stays single-writer).
- Acceptance: one manual run end-to-end produces shadow rows visible in the dashboard; zero broker calls (assert via test that the shadow path cannot reach a live broker).
- The shadow week's exit criteria (reviewer judges, ~1 week of trading days): shadow decisions match the gated paper lane's decisions on the same events; no operational failures (missed ticks, auth expiries, feed gaps).

## Hard rules
1. **Zero real orders anywhere this shot.** Robinhood account stays $0; `place_equity_order` is never invoked (tests stub it; the live path proves itself via `review_equity_order` only). Alpaca orders only against the *paper* host.
2. The pilot protocol in the plan is law: when live day comes it's $500 / $50-per-trade / manual approval first 10 / pilot P&L gates nothing. Build the rails to enforce it; never weaken them for convenience.
3. `RiskEngine`, PIT guards, existing tests, study cells, the running paper race: untouched (2-R3 migration excepted).
4. Secrets: tokens under `secrets/` (gitignored), never logged, never in reports. The smoke pattern from `scripts/smoke_test_keys.py` applies.
5. All broker behavior testable offline via fixtures. If blocked (e.g. OAuth registration closed), document and stop — never improvise around an auth wall.

## Completion report (`reports/shot4-report.md`)
- Files added/changed (one line each); test count before/after + tail
- 2-R3: migration proof on the live paper DB (row counts before/after, dashboard join switched)
- 2c-F2: reconcile script demo + the constants change with measurement citations (or documented wait on the settled OpenAI number)
- 5a: the auth answer (token lifetime/refresh, documented in the reference doc), Robinhood read-path evidence, the review_equity_order round-trip output, Alpaca full-lifecycle log, kill-switch + approval-CLI demo
- 5b: shadow run log excerpt, dashboard screenshot-equivalent (lane visible), the no-broker-reachable test
- Deviations with reasons; open questions for the reviewer

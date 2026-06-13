# Strategy Proposal: Catalyst Synthesis on the Small-Cap Long Tail

A real attempt at a novel, defensible agentic strategy. Not a guarantee — a *thesis about where the edge lives* and how to extract it.

---

## 1. The Thesis in One Paragraph

There is a structural inefficiency at the intersection of **(a) small/mid-cap names ($100M–$3B market cap)**, **(b) public-but-dense catalyst data (SEC filings, FDA actions, federal contracts, court rulings, insider Form 4s)**, and **(c) the 1–5 day window the market takes to fully price these events on under-covered names**. The inefficiency exists because traditional special-situation funds are gated by analyst headcount — they cover 30–100 names a person. An LLM agent can cover 3,000 names continuously, read every filing as it drops, synthesize across domains in seconds, and surface only the 1–3 high-conviction setups per week. We're not predicting prices; we're racing slow institutional readers on names they've never heard of.

Why this can work where most LLM trading fails:
- We're not predicting. We're *interpreting events that already happened*.
- We're not competing with HFT — the names trade 50k–500k shares/day; HFT isn't there.
- The data is public and free.
- The LLM's strength (reading dense regulatory text and synthesizing across sources) is the actual job.
- Failure modes are bounded: small positions, many uncorrelated bets, hard time-stops.

Why it can fail:
- Most "catalysts" are already in the price.
- Small caps have wide spreads; slippage eats returns.
- An LLM that hallucinates one decimal on an FDA dose or one clause in a contract value can blow a position.
- 90-day backtests on this pattern look great because of survivorship bias.

The strategy is designed around accepting both — limit position size, require multi-source confirmation, paper-trade first, hard-stop after consecutive losses.

---

## 2. The Edge Mechanism

For each catalyst class below, the edge isn't "the LLM is smarter." It's a specific market microstructure fact about *how that information disseminates*.

| Catalyst Class | Why It Mispricing Persists | LLM Job |
|---|---|---|
| **Spinoffs** | Forced selling by index funds (parent's S&P holders dump the spin); no analyst coverage day-one | Read Form 10 / S-1; identify whether spin has hidden quality |
| **FDA CRL / approval on micro-pharma** | One-line FDA letter, no analyst note for hours | Translate the FDA letter; identify if drug is salvageable |
| **Federal contract awards** | Posted to SAM.gov / USASpending; takes hours for a human to notice | Match award $ to company revenue; compute materiality |
| **Insider cluster buys** | Form 4 filings appear staggered; pattern only visible if you watch all of them | Detect ≥3 insiders buying within 30 days, unusual vs prior 2yr |
| **NT 10-K / NT 10-Q filings** | Filing delay announcement — often predictive of bad news | Read the reason; classify as benign vs ominous |
| **8-K material event language drift** | Buried in a Friday 4:30pm 8-K | Compare current 8-K language to last 4 quarters for the same event type |
| **Post-bankruptcy emergence equity** | Fresh-start equity issued to creditors who dump regardless of value | Read POR; estimate post-emergence cash + earning power |
| **Activist 13D filings** | First few hours after filing, before media pickup | Read the 13D exhibit; classify activist seriousness |
| **Patent litigation rulings** | PACER dockets, no consumer-facing alert | Read motion ruling; map to revenue at risk |
| **Congressional disclosures (delayed)** | Filed days after the trade; small-cap signals get ignored | Filter for non-obvious trades in undercovered names |

Each individual catalyst is a small edge. The portfolio strategy is to run **all of them in parallel**, take only the highest-conviction setups, size small, and let the law of large numbers work.

---

## 3. Data Sources (all free or <$100/mo)

### Free, real-time
- **SEC EDGAR full-text search & RSS** — `https://www.sec.gov/cgi-bin/browse-edgar`, `https://efts.sec.gov/LATEST/search-index?q=...`. Full filings within seconds of dissemination.
- **EDGAR Form 4 feed** — `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4`. Insider transactions.
- **ClinicalTrials.gov API** — phase changes, terminations, primary completion delays.
- **FDA Drug Approvals** — `https://api.fda.gov/drug/`. Approvals, recalls, labeling changes. CRLs not directly published but inferable from 8-Ks.
- **USASpending.gov API** — `https://api.usaspending.gov/`. Federal contract awards within ~24h of obligation.
- **SAM.gov contract opportunities feed** — pre-award notices.
- **Federal Register API** — `https://www.federalregister.gov/api/v1/`. All regulatory actions.
- **USPTO Bulk Data + PEDS API** — patent issuances, oppositions.
- **DOJ Press Releases** — RSS, all DOJ actions.
- **Congressional Trading Disclosures** — House Clerk + Senate eFD portals (raw); free aggregators rebuild this.
- **FCC, FERC, FTC actions** — each has free feeds.
- **WARN Act notices** — state-level, free, mostly scraped from labor department sites.
- **Bankruptcy filings** — PACER (~$0.10/page, capped at $3/doc); free CourtListener RECAP archive for already-pulled docs.

### Market data (Robinhood-adjacent or cheap)
- **Robinhood MCP** — positions, orders, account state.
- **Polygon.io** — $29/mo basic, real-time-ish small-cap quotes, options chains, fundamentals. Best price/value on the market for our use case.
- **Yahoo Finance unofficial endpoints** — free, fine for nightly batch.
- **Tiingo** — $10/mo, clean fundamentals & news.

### Optional paid intel ($50–200/mo each, add later)
- **Quiver Quantitative** — pre-parsed congressional trades + government contracts.
- **OpenInsider** — free Form 4 aggregator, easier than parsing yourself.
- **WhaleWisdom** — 13F changes (lagged but useful for the bankruptcy-emergence playbook).

**Day-one stack:** EDGAR + Polygon + Robinhood MCP + USASpending + ClinicalTrials.gov + OpenAI/Anthropic API. Total ~$50/mo plus LLM token cost. Everything else added if/when a specific catalyst class starts producing trades.

---

## 4. Agent Architecture

Five specialist agents + one coordinator + a deterministic state store. The state store is the most important design rule: **the LLM never holds positions, cash, or risk limits in its context — only the SQLite DB does**. The LLM proposes; the DB authorizes.

```
                ┌─────────────────────┐
                │   Deterministic     │
                │   State Store       │
                │   (SQLite)          │
                │ - positions         │
                │ - cash              │
                │ - risk limits       │
                │ - thesis log        │
                │ - audit trail       │
                └──────────┬──────────┘
                           │
   ┌───────────────────────┼───────────────────────┐
   │                       │                       │
┌──▼────┐  ┌──────┐  ┌──▼──────┐  ┌─────────┐  ┌──▼────┐
│Watcher│  │Triage│  │Synthesis│  │Risk/Size│  │Trader │
│agents │─▶│agent │─▶│  agent  │─▶│  agent  │─▶│ agent │
│(N=10) │  │      │  │         │  │         │  │  MCP  │
└───────┘  └──────┘  └─────────┘  └─────────┘  └───────┘
                                                    │
                                              ┌─────▼─────┐
                                              │   Exit    │
                                              │ monitor   │
                                              │  agent    │
                                              └───────────┘
```

### Watcher agents (10, one per data source)
- Pure code, no LLM. Cron schedules per source's natural cadence (EDGAR every 60s, USASpending hourly, ClinicalTrials.gov daily).
- Dedupe against state store; push new events to a "raw events" queue.

### Triage agent (cheap LLM — Haiku or GPT-mini)
- Reads each raw event with a tight prompt: "Is this a catalyst on a single $100M–$3B company? Output: ticker, catalyst_class, materiality_score 0-10, raw_summary."
- 90% of events get rejected here (most are irrelevant or too small).
- Cheap model, high volume — this is the bottleneck filter.

### Synthesis agent (best available model — Opus, GPT-5, whatever's strongest)
- Only runs on triage outputs with materiality ≥ 6.
- Gets a rich context: the raw filing, last 4 quarterly 8-Ks for the same ticker, current price & 30-day range, options skew if available, short interest, recent insider activity, last earnings call transcript excerpt.
- Produces a **structured thesis**:
  ```
  ticker: HMNI
  direction: long
  catalyst: NDA acceptance for lead candidate, ahead of consensus timeline
  thesis: <2-3 sentences>
  expected_repricing_window: 3-10 days
  invalidators: <list of news that would kill the thesis>
  confidence: 0.72
  similar_historical_setups: <if any in thesis log>
  ```
- Each field is required. Confidence < 0.6 → discard.

### Risk/Sizer agent (deterministic code + LLM sanity check)
- **Hard rules (code, not LLM):**
  - Per-position cap: 3% of account.
  - Per-sector cap: 25%.
  - Daily new-position cap: 4.
  - No new positions if last 5 closed trades had >3 losses.
  - No new positions if account drawdown > 8% from 30-day high.
  - Skip ticker if avg daily volume < $500k (slippage kill).
  - Skip if bid-ask spread > 1% of mid.
- **LLM check:** "Given current portfolio composition and this thesis, is the position size reasonable? Anything correlated already on?"
- Output: size in $, or REJECT.

### Trader agent (deterministic code, calls Robinhood MCP)
- Places limit orders at mid + 0.3% for buys, mid - 0.3% for sells.
- Retries 3x with widening spread.
- If unfilled after 60s, abandons (this preserves capital for cleaner setups).

### Exit monitor agent (cheap LLM, runs every hour during market hours)
- For each open position, checks: thesis invalidators triggered? Time-stop hit? Target hit?
- **Time-stop rule:** Most theses are 3–10 day windows. Hard exit at 14 days regardless of P/L. Catalysts that haven't worked by then almost never do.
- **Profit-take:** scale out 50% at +15%, trail rest with a 7% stop.
- **Hard stop:** -8% per position, no negotiation.

---

## 5. Entry / Sizing / Exit — Specifics

### Entry checklist (all must pass)
1. Thesis confidence ≥ 0.65.
2. Liquidity: ADV ≥ $500k, spread ≤ 1% of mid.
3. Market cap: $100M–$3B.
4. Listed on NYSE/NASDAQ (Robinhood supports — no OTC).
5. No earnings within next 5 trading days (unless earnings *is* the catalyst).
6. Not already in portfolio.
7. No correlated position (same sub-sector & cap range).
8. Risk/sizer agent didn't veto.

### Sizing
- Base size = (account × 3%) × confidence-multiplier where multiplier = (confidence - 0.5) × 2, capped 1.0.
- Confidence 0.65 → 0.9% of account.
- Confidence 0.85 → 2.1% of account.
- Confidence 1.0 → 3.0% of account (cap).

### Exit
- Profit-take 50% at +15%, trail rest with 7% trailing stop on highs.
- Hard stop -8%.
- Time-stop 14 days.
- Thesis-invalidator exit: any time a watcher surfaces news matching the invalidator list, full exit immediately.

### Position-count target
- 8–15 open positions at steady state.
- Each held 3–14 days.
- ~3–5 new entries/week, ~3–5 exits/week.

---

## 6. The "Live Thesis Log" — Why This Is Non-Negotiable

Every thesis the synthesis agent generates — accepted *or rejected* — is logged with:
- Full prompt input.
- Full thesis output.
- Resulting decision.
- 30 days later: outcome (price change, whether invalidator triggered, whether thesis was correct in *direction* regardless of P/L).

This serves three purposes:
1. **Debugging.** When the bot loses money, you replay the thesis chain and see exactly where the LLM was wrong (data? reasoning? sizing?).
2. **Strategy refinement.** After 3 months, you can compute catalyst-class-level hit rates and prune the categories that don't work.
3. **Detecting drift.** LLMs change. If hit rate degrades, you can pin it to a model update.

This log is the single most valuable asset the system produces — more valuable than the P&L.

---

## 7. Honest Failure Modes & Counter-Designs

| Failure Mode | Counter-Design |
|---|---|
| LLM hallucinates a contract value or FDA approval status | Cross-check every numeric claim against the source document with a deterministic regex/parse before sizing. If can't extract, reject. |
| Backtest looks great, live trading doesn't | We don't backtest. Paper-trade live for 60 days minimum. |
| Slippage destroys returns on small caps | Hard liquidity floor, mid-quote limit orders, abandon if unfilled in 60s. |
| All theses converge during regime change | Daily-loss cutoff (no new entries after -3% day); 30-day-drawdown cutoff (no new entries after -8% drawdown). |
| Insider-cluster signals are widely arb'd | Demote insider-cluster to materiality cap of 7; require it to combine with a second catalyst type to fire. |
| Anthropic / OpenAI changes model behavior | Pin model version. Re-run last 30 days of logs through new model to detect drift before promoting. |
| Robinhood rate-limits the MCP | Throttle order placement to ≤1/sec. Designed for low-frequency anyway. |
| Spinoff data is too special-case for LLM | Build a specialist sub-agent with a tighter prompt and a checklist (parent indexes? mandatory selling? dividend recapture? etc.) |

---

## 8. The 90-Day Validation Plan

You can't know if this works until you run it. The plan to find out *without* losing real money:

**Days 1–14: Build minimum viable agent.**
- One catalyst class only: federal contract awards on $100M–$3B companies. Simplest data, clearest materiality math. Skip everything else.
- Watcher: USASpending API poll, hourly.
- Triage + synthesis: single combined prompt for v1.
- Sizing: paper only, fixed $1k per position.
- Exit: time-stop only, 14 days.
- Goal: 20 paper trades logged with theses.

**Days 15–30: Add catalyst classes.**
- Add EDGAR 8-K reader, Form 4 cluster detector, FDA approvals.
- Move to multi-agent architecture (split triage from synthesis).
- Still paper. Goal: 50 trades, hit rate by catalyst class, average return per class.

**Days 31–60: Tune and harden.**
- Whichever 2–3 catalyst classes have ≥55% directional hit rate and positive average return, keep. Drop the rest.
- Add thesis-invalidator monitoring.
- Add risk/sizer logic.
- Still paper. Goal: 100 trades, by-class stats stable.

**Day 61: Decision point.**
- If 60-day paper P&L is positive net of an assumed 0.3% round-trip slippage AND hit rate ≥ 55% directional, go live with **$1,000 real money**, fixed $50/trade.
- If not, return to days 15–30 with a different catalyst-class mix. Don't go live.

**Days 62–90: Real money, tiny size.**
- $50/trade × maybe 10 positions = $500 at risk. Tuition.
- Track slippage vs paper. This is the single most important metric — if real slippage is >2x paper assumption, the model breaks.
- If 30-day real-money P&L is positive net of all fees, raise stake to $2,000 account, max 3% positions.

**Day 91 and beyond:**
- If working: keep scaling with a hard rule that account doubles require 60 days of positive P&L at each tier.
- If broken: stop. Keep the thesis log. Iterate offline.

---

## 9. What I Genuinely Don't Know

- **Whether the slippage on $500k ADV names eats the edge.** This is the biggest unknown. Paper-trading masks it. Only real fills tell you.
- **Whether catalyst hit rates degrade as the strategy scales.** Probably not at this size — we're talking $10k positions, not $10M — but possible.
- **How much the LLM-as-thesis-generator improves vs deterministic rules.** A simple rule-based system ("buy any small cap with a ≥3-insider cluster in 30 days") might capture most of the edge. The LLM's value is in *filtering false positives*, which only the thesis log will measure.
- **Whether Robinhood's MCP rate limits or beta restrictions kill it.** It's beta. They might rate-limit. They might pull the feature. Have a contingency to port to Alpaca (free API, similar capability).

---

## 10. Why This Beats Picking a Framework Off GitHub

Most public agentic-trading projects fail one or more of:
- **No clear edge mechanism** — they assume LLM-as-oracle will produce alpha. It won't.
- **Universe is liquid large caps** — that's the hardest place to win.
- **No deterministic state** — LLM holds positions in context, hallucinates them.
- **No invalidator framework** — they enter and pray.
- **Backtest-driven** — survivorship bias and look-ahead leak in.
- **No paper-to-live ramp** — straight to real money, blow up.

This strategy is specifically designed around what an LLM agent uniquely does well (read dense text fast across many sources) on a universe where institutional competition is structurally limited (analyst-headcount-bound special-situation funds). It's not the only viable strategy — but it's a real one, not a vibes one.

---

## 11. Recommended Next Step

If you want to actually try this: I can scaffold the project structure for stage-1 (single catalyst class, federal contracts, paper-trading harness, thesis log) in this directory. That's maybe 600–800 lines of Python — one watcher, one combined LLM agent, one SQLite schema, one Robinhood MCP integration in paper mode, one Polygon price feed. It's the minimum you need to start generating real thesis logs.

Say the word and I'll start building. Otherwise this doc stands as the design.

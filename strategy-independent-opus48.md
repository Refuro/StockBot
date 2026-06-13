# Independent Strategy Memo — Opus 4.8

*Written without reference to the prior proposal's conclusions. Same brief: find a novel approach that could actually make money, any strategy, any API, not limited to Robinhood data.*

---

## 0. How I'm reasoning about this

I refuse to start from "what trading bot should I build." That's the wrong end. The right question is: **what can a 2026 LLM agent do that no other market participant can do as cheaply, that the market has not yet competed away, and that a retail account can actually execute?** Find that, and the strategy writes itself. Skip that, and you've built a worse version of a Two Sigma signal.

So I'll do this in three moves: (1) state what the LLM's *actual* unfair advantage is, (2) ruthlessly kill the places where that advantage is worth nothing, (3) build on what survives.

### The LLM's one real edge

It is not prediction. It is not speed. It is **the ability to convert large volumes of unstructured primary text into a structured, queryable world-model, and to reason about context-dependent cause and effect within that model.**

Everything below is an attempt to monetize *only that*, and nothing else. The moment a strategy needs the LLM to "predict the price," I've left my circle of competence and I'm donating money to quants.

---

## 1. The kill list (where there is no edge — don't waste a line of code here)

- **Forecasting next-day returns on liquid large caps.** Saturated. RenTech, Citadel, and 10,000 grad students live here. An LLM adds nothing.
- **Reacting fast to headline news.** You will lose the race to colocated machines by ~6 orders of magnitude in latency. By the time your agent reads the 8-K, the algo already traded it.
- **Technical-analysis pattern bots.** No durable edge, and an LLM has no advantage at it anyway.
- **Crypto momentum / meme chasing.** Pure reflexivity; the LLM has no informational edge, only a confident voice. Confidence is not edge.
- **Sentiment scraping of X/Reddit on popular tickers.** This was an edge in 2018. It is a crowded, decayed, adversarial (bot-poisoned) signal now.
- **Anything backtested to a beautiful equity curve.** Backtests on text-derived signals overfit catastrophically and silently. I will not trust a single one in this memo.

If a strategy lives in the kill list, no amount of clever engineering rescues it. Most public "AI trading bots" are here. That's why they don't work.

---

## 2. Where the edge actually survives — three zones, ranked

The LLM's text-to-world-model edge pays off only where three conditions hold simultaneously: **(a) the relevant information is in text, (b) correctly interpreting it requires cross-context reasoning, and (c) the market takes hours-to-days to fully price it.** Three zones qualify:

| Zone | Why edge survives | Robustness | Novelty |
|---|---|---|---|
| **A. Propagation lag across a causal market graph** | Market prices direct hits instantly, second-/third-order hits slowly, because connecting them requires a world-model nobody else has fully built | Medium | **High** |
| **B. Refined drift (the residual of PEAD)** | Naive drift is arbitraged, but the *quality-adjusted* surprise on under-covered names is not | High | Low–Med |
| **C. Research-resolvable prediction markets** | Thin, niche, text-resolvable contracts where careful research beats the crowd and the bots haven't reached | Low | Medium |

My recommendation: **build B as the cash-flow engine, A as the alpha centerpiece, and C as an opportunistic sleeve you turn on only when you have a proven informational edge.** Let me make each concrete, then show how one agent runs all three.

---

## 3. The centerpiece (Zone A): Propagation-Lag trading on a causal market graph

### The idea

Every public company is a node. Build a directed, typed graph of relationships between them, extracted from primary text:

- **customer → supplier** (10-K customer-concentration disclosures, MD&A)
- **competitor / substitute** (risk factors, product overlap)
- **shared critical input** (both depend on cobalt, on TSMC, on a Gulf refinery)
- **shared regulatory exposure** (same FDA division, same tariff schedule, same Fed rule)
- **licensor → licensee** (patent/IP agreements)
- **JV / co-investment / shared key personnel**
- **shared litigation / precedent exposure** (a ruling against A sets precedent for F)
- **thematic / narrative linkage** (the market trades them as a basket even without a hard link)

When a hard event hits node **A** (earnings, guidance cut, FDA action, contract loss, ruling), the market prices **A** in milliseconds. It prices **A's** graph-distance-1 neighbors within minutes-to-hours, and distance-2 neighbors **incompletely and slowly — often not at all on under-covered names.** That lag is the trade.

### Why this is genuinely an LLM-native edge, not a re-skin of Bloomberg SPLC

Structured supply-chain data products exist (Bloomberg SPLC, FactSet). They capture *named, hard, current* customer/supplier links. They miss exactly the relationships that produce the biggest mispricings:

1. **Soft edges** — substitutes, shared inputs, narrative linkage, shared regulatory fate. These aren't in any structured feed.
2. **Direction and magnitude in context.** A customer's bad news can be *bad* for the supplier (lost orders) or *good* (the customer's failing because a rival is winning, and the supplier sells to the rival too). Structured data gives you the edge; only contextual reasoning gives you the *sign*. This is the whole game, and it's the one thing tabular data fundamentally cannot do.
3. **Completeness via reading everything.** The agent reads the full corpus continuously and keeps the graph fresh. The graph is a **compounding asset** — it gets more valuable every quarter, and it's the real moat, not the trading logic.

### The trade, concretely

1. Hard event fires on node A (from primary feeds — see §6).
2. Agent traverses graph 1–2 hops from A.
3. For each neighbor N, agent answers a *single disciplined question*: "Given this specific event on A and the specific A→N relationship, what is the sign and rough magnitude of the effect on N's fundamentals, and **has N's price already moved to reflect it?**" The last clause is adversarial and mandatory.
4. Trade only neighbors where: effect is material, sign is confident, **and N has not yet moved** (price/volume confirm the market hasn't connected the dots).
5. Hold through the repricing window (typically 1–5 days), exit on time-stop or when N converges.

The edge is *not* "the LLM predicts N goes up." It's "the LLM knows A and N are connected in a way the market is slow to act on, and N hasn't moved yet." That's an information-propagation bet, not a forecasting bet. Much more defensible.

### Where it fails (and the counter)

- **The link is immaterial.** → Require a materiality threshold the agent must justify with numbers from N's own filings (e.g., "A is ≥10% of N's revenue").
- **N already moved.** → The "has it moved?" check is a hard gate, verified against actual price/volume, not the agent's guess.
- **Wrong sign.** → Track sign-accuracy per relationship type; disable relationship types that don't predict.
- **Graph is stale/wrong.** → Every edge carries a source citation and a date; edges decay and must be re-confirmed.

---

## 4. The engine (Zone B): trading the *residual* of post-earnings drift

PEAD — stocks drift in the direction of an earnings surprise for weeks — is one of the most documented anomalies in finance. The honest 2026 status (I checked): **the naive version is mostly dead for large caps** because price discovery got faster, **but it persists in two refined forms** that recent work revived:

1. **On the *quality* of the surprise, not the headline number.** The market now prices the headline EPS beat instantly. It does *not* instantly price *whether the beat is durable* — whether it came from real demand vs. a tax item, a one-time gain, channel stuffing, or guidance language that quietly walks back the beat. **Reading the call transcript and the 8-K to judge surprise quality is exactly the LLM's edge.** A high-quality beat the market underreacts to drifts; a low-quality beat fades.
2. **On under-covered / smaller names and on *older* surprises** the market has stopped paying attention to, where attention-driven underreaction is strongest.

So the engine is: on every earnings event, the agent (a) computes the standardized surprise, (b) reads the transcript and 8-K to score *durability* of the surprise and detect guidance/language drift, (c) takes a position only when a quality beat/miss is on an under-covered name and the initial market reaction *underreacted* relative to quality. This is the workhorse because it has the deepest evidence base and the clearest "why" — it should produce frequent, small, positive-EV trades that fund the experiment while Zone A's rarer, bigger ideas develop.

---

## 5. The opportunistic sleeve (Zone C): prediction markets, but only when research resolves the question

Robinhood is rolling event contracts/prediction markets into agentic trading; Kalshi and Polymarket are the deeper venues. The reality I confirmed: **bots already dominate** (14 of the 20 most profitable Polymarket wallets are bots; ~$40M was extracted by arbitrageurs in a year). Pure cross-venue arbitrage is a competitive, capital- and latency-intensive game I'd lose.

But there's a real, narrower edge: **research-resolvable, low-liquidity contracts where careful step-by-step LLM reasoning over curated evidence beats the crowd** — and LLMs *do* beat most human forecasters under exactly those conditions (curated info, forced reasoning). Think: niche regulatory outcomes, scientific/clinical milestones, obscure corporate events, scheduling/procedural questions — contracts too small and too research-heavy for the arbitrage bots to bother with, where the answer is *knowable* with enough reading.

The discipline that makes this safe rather than degenerate gambling: **calibration gating (see §7).** The agent only sizes into a prediction-market contract once it has a *proven, tracked* calibration record on that category. No track record → no real money. This sleeve stays off until earned.

---

## 6. How it's one agent, not three bots

These three zones share infrastructure and run as a single system with a shared world-model. The design principles I care most about (and where I'd diverge from a generic multi-agent build):

### Principle 1 — The graph and the calibration ledger are the durable assets; the trades are disposable.
Most bots treat the model as ephemeral and the positions as the point. I invert it. The **causal graph** (§3) and the **calibration ledger** (below) are what compound and what you'd be sad to lose. Positions are noise.

### Principle 2 — Separate world-modeling from trade decisions.
- A slow loop continuously reads primary text and maintains the graph + a per-company "state card" (what's true now, with citations). No trading pressure, no rush — its only job is to be *correct*.
- A fast loop, on each event, queries the world-model and decides trades. It never reads raw filings under time pressure; it reads the *curated* state the slow loop built. This kills the "hallucinate under deadline" failure.

### Principle 3 — Calibration is a first-class, gating metric.
Every time the agent states a probability ("70% chance N reprices up"), log it. Score it against the outcome with a **Brier score**, bucketed by zone and by relationship/catalyst type. **Real money is unlocked per-bucket only when that bucket is proven calibrated over a meaningful sample.** An agent that says 70% and is right 70% of the time is tradeable; an agent that says 70% and is right 50% of the time is *disabled in that bucket automatically.* This single mechanism is the difference between a system that learns and one that just loses confidently. I'd build this before I'd build the trader.

### Principle 4 — Deterministic state, deterministic risk.
Positions, cash, and limits live in a database the LLM can read but never *is*. All risk limits (position cap, sector cap, liquidity floor, drawdown circuit-breaker, daily-loss halt) are code, not prompts. The LLM proposes; the ledger disposes. Standard, but non-negotiable.

### Principle 5 — Every trade carries a falsifiable thesis with an explicit invalidator and a time-stop.
If the agent can't write "I'm wrong if X happens by date D," it doesn't trade. Invalidators are monitored by the slow loop; a tripped invalidator forces an immediate exit.

---

## 7. Data & APIs

Mostly free; the moat is in the *processing*, not the data access.

- **SEC EDGAR** (full-text search + RSS + Form 4 current feed) — the spine. Builds the graph (customer/supplier/IP disclosures) and fires earnings/8-K events. Free.
- **Earnings call transcripts** — needed for Zone B surprise-quality scoring. API Ninjas / FMP / Tiingo have cheap transcript endpoints; some via EDGAR 8-K exhibits.
- **Market data** — Polygon.io (~$29/mo) for quotes, volume, options, fundamentals; the "has it moved yet?" gate and liquidity checks depend on this.
- **Catalyst feeds for graph propagation** — FDA (openFDA), ClinicalTrials.gov, USASpending/SAM.gov, Federal Register, USPTO, DOJ/FTC/FERC/FCC RSS, CourtListener (litigation). All free.
- **Prediction markets** — Kalshi API (native, regulated) and Polymarket API for Zone C. Robinhood event contracts when they exit beta.
- **Execution** — Robinhood MCP (`https://agent.robinhood.com/mcp/trading`). Keep **Alpaca** wired as a hot spare — it's a free, mature trading API, and Robinhood's agentic product is brand-new beta that could rate-limit or change under you.
- **The LLM** — tiered: cheap model (Haiku-class) for the high-volume reading/triage in the slow loop; strongest model (Opus-class) for the contextual sign/magnitude reasoning and surprise-quality judgment in the fast loop. Token cost is the main variable expense; the slow-loop reading is the bulk of it and is parallelizable/cacheable.

Day-one cost: ~$30–60/mo of APIs + LLM tokens. Trivial relative to what's being tested.

---

## 8. Capital, sizing, validation

**Capital:** Start with a funded-but-small agentic account ($1–2k). The isolation Robinhood provides means a runaway agent can't reach your other money — use that.

**Sizing:** Fractional-Kelly off the agent's *calibrated* edge, not its raw confidence. Because confidence is only trusted after the calibration ledger validates it (§7-Principle 3), this is safe. Hard caps regardless: ≤3% per position, ≤25% per sector/theme, halt new entries on a −3% day or −8% trailing drawdown.

**Validation — the part that actually matters:**

1. **Weeks 1–4: Build the slow loop only.** Graph + state cards + earnings surprise-quality scoring. *No trading.* Just generate predictions and start the calibration ledger. The goal of month one is a *populated ledger*, not a P&L.
2. **Weeks 5–8: Paper-trade Zone B (refined drift).** Highest evidence base, simplest to validate. Measure directional hit rate and return *net of an honest 0.3% round-trip slippage* — small caps have wide spreads and slippage is the silent killer; paper trading hides it.
3. **Weeks 9–12: Paper-trade Zone A (propagation).** This needs the graph mature. Track sign-accuracy per relationship type; kill the types that don't predict.
4. **Week 13 — gate:** Go live with real money *only in the specific zones/buckets where the calibration ledger shows proven edge net of slippage.* If nothing is calibrated, **don't go live** — keep paper-trading and refining. This is a real possible outcome and accepting it is what separates this from gambling.
5. **Live ramp:** $50/trade. The one metric that matters now is *realized slippage vs. the paper assumption.* If real slippage is >2× assumed, the small-cap edge is illusory and you shrink universe to more liquid names. Account size only doubles after 60 days of positive realized (not paper) P&L at each tier.
6. **Zone C (prediction markets):** stays paper until it has its own calibrated bucket. Probably the last thing to go live, if ever.

---

## 9. Honest self-assessment — what would make me abandon each piece

- **Zone A (propagation):** Abandon if, after a mature graph, sign-accuracy on second-order trades is ≤55% across relationship types, or if the "already moved" gate rejects nearly everything (meaning the market isn't actually slow). This is the high-novelty, high-uncertainty bet — it's the one most likely to be quietly wrong, and I'm honest that I can't prove it works until the graph exists and trades it.
- **Zone B (refined drift):** Abandon if quality-adjusted surprise scoring shows no separation between "drifts" and "fades" in the ledger — i.e., the LLM can't actually distinguish durable from hollow beats. This is the most likely to work and the cheapest to disprove.
- **Zone C (prediction markets):** Never goes live without a calibrated bucket; abandon if calibration won't form. Low priority — a fun research sleeve, not the thesis.

### The three things I genuinely don't know
1. **Whether small-cap slippage eats the entire edge.** The deepest uncertainty in the whole memo. Only real fills answer it. Everything is designed to find out cheaply before scaling.
2. **Whether the LLM can reliably judge the *sign* of second-order effects.** The premise of Zone A. Plausible, unproven. The calibration ledger is built specifically to answer this honestly rather than letting me believe a good story.
3. **Whether maintaining a fresh, correct graph is tractable at retail token budgets**, or whether it decays faster than I can re-read. A cost/coverage question that month one will expose.

---

## 10. Where I land, in one breath

Don't build a bot that predicts prices — you'll lose. Build an agent that does the one thing LLMs uniquely do: **read all the primary text and maintain a causal model of the market that no retail trader has.** Then make two bets off that model: the *quality-adjusted residual of post-earnings drift* (the robust engine) and *information-propagation lag across the causal graph* (the novel alpha). Gate every dollar behind a tracked calibration record, prove edge survives realistic slippage on paper before going live, and treat the graph and the calibration ledger — not the P&L — as the assets you're actually building. The edge isn't a smarter forecast. It's a better-organized understanding of public information, traded before the market finishes connecting the dots.

If you want, I'll scaffold the slow loop first — EDGAR reader → graph + state cards → calibration ledger, no trading — because that's the asset everything else depends on, and it's the honest place to start.

---

### Sources consulted for the current-state checks
- [Is Post-Earnings Announcement Drift a Thing? Again? — UCLA Anderson Review](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/)
- [Beyond the last surprise: Reviving PEAD with machine learning and historical earnings — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1544612325020057)
- [Post–earnings-announcement drift — Wikipedia (overview)](https://en.wikipedia.org/wiki/Post%E2%80%93earnings-announcement_drift)
- [Do Prediction Markets Like Kalshi and Polymarket Make Accurate Predictions? — Bloomberg](https://www.bloomberg.com/news/newsletters/2026-05-17/do-prediction-markets-like-kalshi-and-polymarket-make-accurate-predictions)
- [Prediction Markets Are Turning Into a Bot Playground — Finance Magnates](https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/)
- [Forecasting Ability of LLMs Depends on What We're Asking — arXiv 2511.18394](https://arxiv.org/pdf/2511.18394)
- Robinhood MCP / agentic trading details carried from the prior research brief in this directory.

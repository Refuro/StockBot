# Two Engines and a Lens — Project Overview

*A plain-English guide to the whole system, written for someone with basic
investing experience. Last updated 2026-06-13.*

---

## 1. What this project is, in one breath

This is an **automated trading research system**. It hunts for a handful of
specific, repeatable stock-market edges, and — this is the important part —
it is built to **prove an edge is real with fake money before it ever risks a
real dollar**, and to **kill any idea that turns out to be luck**. The trading
ideas are the easy part. The discipline that stops us fooling ourselves is the
whole point.

The name: **two "engines"** (each engine is a family of trading strategies) and
**one "lens"** (a sensing tool that doesn't trade — it watches the crowd).

There is one running program. A config switch puts it in one of three modes:

- **Backtest** — replay historical data to see how a strategy *would* have done.
- **Paper** — trade live market data with **fake money**, in real time, every day.
- **Live** — trade **real money** through a broker. We are not here yet, on purpose.

Everything below is about how we move responsibly from "interesting idea" to
"allowed to touch real money."

---

## 2. The core beliefs (why it's built this odd, careful way)

Most trading systems fail because their creators fool themselves. Ours is
engineered against that. Five rules run through everything:

1. **Pre-registration.** Before we look at any results, we write down — *in
   code* — exactly what we're testing, what counts as success, and what number
   would prove us wrong. You can't move the goalposts after seeing the data if
   the goalposts were nailed down first.
2. **Champion vs. challenger ("brain vs. coin").** Every "smart" strategy races
   an identical **dumb twin** that flips a coin. If the smart version can't beat
   the coin, the smartness is worthless and we cut it. This is the single most
   honest test in the system.
3. **The AI never sizes a trade.** Language models (Claude, GPT) read filings
   and estimate probabilities. They **never** decide how many shares to buy or
   touch the risk limits. Deterministic, auditable code does that.
4. **Gates before money.** A strategy must pass specific, pre-set statistical
   bars before it's allowed near capital — and even then it starts at $50 a
   trade. Profit in the pilot earns *nothing*; only passing the gates does.
5. **Calibration over bravado.** We don't ask "did it make money." We ask "when
   it said 70%, did 70% happen?" A strategy that knows what it doesn't know is
   worth more than one that's loud and occasionally right.

There's also a **builder/reviewer split**: AI "builder" agents write the code in
big batches ("shots"); a separate AI "reviewer" (a different model, in a
different tool) independently re-runs everything, grades it against the
pre-registered bars, and is the only one allowed to make gate calls. Builders
never grade their own work and never hold the keys to real money.

---

## 3. The moving parts (the machine under the hood)

| Part | What it does, plainly |
|---|---|
| **Data feed** | Supplies prices and events (earnings, SEC filings). It enforces **"point-in-time" (PIT)** honesty: the system can never peek at data from the future. If it tries, the program crashes on purpose. |
| **Broker** | Executes orders. In paper mode it's a **simulator** (models slippage and costs); in live mode it's **Robinhood** (or Alpaca as a backup). |
| **Risk engine** | Hard, dumb, non-negotiable limits: max % per position, daily-loss halt, drawdown halt, max orders/day. The AI cannot soften these. |
| **Strategy** | The actual trading logic. All trading lives here; the surrounding machinery is identical across backtest/paper/live so a strategy behaves the same everywhere. |
| **Calibration ledger** | The scorekeeper. Every prediction ("X, 65% chance, by this date") is logged, then graded when it resolves. This ledger is the gate that controls capital. |
| **State store** | A database (SQLite) that remembers everything — positions, fills, predictions, decisions — so the daily restart picks up exactly where it left off. |
| **Dashboard** | A read-only web page at `127.0.0.1:8765` that shows the race, positions, scores, and plain-English explainers. |

---

## 4. The two engines and the lens (the actual strategies)

### Engine 1 — The Proportionality Engine
This engine bets on the market **mis-reacting to news**. It has two sides:

- **Ride side (post-earnings drift).** When a small company posts an earnings
  *surprise*, its stock tends to keep drifting in that direction for days —
  the market digests news slowly. We ride that drift. Universe: ~1,376 US
  small/mid-caps ($300M–$3B). This is the most-tested, furthest-along strategy.
- **Fade side (crash overreaction).** When a stock **craters ≥10% in a day**, we
  pull the *primary source document* (the actual 8-K, offering, court order —
  never a headline), and an AI rubric judges: was the drop **proportionate** to
  the real dollar damage, or an **overreaction**? If it's a clear overreaction
  with no "binary" risk (no pending fraud/verdict/bankruptcy), we **fade** it —
  bet on a partial bounce. A second AI then plays **devil's advocate** ("argue
  the crash is correct"); if that's persuasive, the trade is vetoed.

### Engine 2 — The Frontier Engine
This engine bets on **shocks traveling through business relationships**. From
SEC filings we built a **map**: which small company depends on which big company
as its **biggest customer**, its **only supplier**, or shares a **regulatory
precedent**. The bet: when a big "anchor" company has a bad day, the small
companies that depend on it should suffer too. *(Status: built, but not yet
proven — see §10 and the separate Frontier assessment.)*

### The Lens — Flow Lens & Crumbs
These **don't trade**. They sense:

- **Flow Lens** logs what a simulated "retail crowd" of AI personas would chew
  on, to later test whether crowd attention predicts anything.
- **Crumbs** scans for **odd-lot tender offers** and other small structural
  signals in filings.

If the Lens proves it sees something real, it becomes an *overlay* that nudges
the engines — but only after it earns it.

---

## 5. The decision-making process (how a trade gets born)

For the AI-judged strategies, every candidate runs a gauntlet:

1. **Triage (cheap AI — Claude Haiku).** "Is this even worth a careful look?"
   Throws out the obvious noise for almost no cost.
2. **Ensemble (two strong AIs — Claude Opus 4.8 + GPT-5.5).** Both read the same
   document and independently score it. If they **disagree by more than 0.15**,
   we take the **lower** (more cautious) number and flag it. If one model is
   down, we cap the confidence and flag it — never silently trust one voice.
3. **Adversarial counter-pass.** For fade trades, the *other* model is told to
   argue the opposite. If it's convincing, the trade is **vetoed** and the veto
   is logged.
4. **Deterministic code takes over.** It checks the calibration gate, applies
   position sizing, runs it through the risk engine, and records the prediction.
   The AI's job ended at "here's my probability."

Spending on the AIs is capped (about **$5/day** for the whole daily run) and the
cost is logged and reconciled against the providers' real bills each month.

---

## 6. How we keep score (the part most systems skip)

We measure four different things, because "it made money" is the easiest number
to fake yourself with:

- **Hit rate** — what fraction of trades were directionally right.
- **Mean return per trade** — average profit/loss after simulated costs.
- **Brier score** — *calibration*. 0 = perfect fortune-teller, 0.25 = no better
  than a coin. A strategy must score **≤ 0.25 over ≥ 10 resolved predictions**
  to be considered "calibrated."
- **Sector-neutral sign-accuracy** (Frontier) — did the stock move the predicted
  way *after subtracting what its whole sector did that week*? This strips out
  "the whole market went down" so we measure the actual edge.

And the killer test — **the brain-vs-coin race.** Each smart lane runs beside an
identical mechanical twin:

| Smart lane | Its dumb twin | What the race answers |
|---|---|---|
| `proportionality` (AI-gated ride+fade) | `proportionality_mech` (constant 0.55, no AI) | Does the AI layer add anything over a coin? |
| (fade inside the gated lane) | `crash_fade_mech` (fades every crash, no judgment) | Is the fade *rubric* worth its cost? |
| `frontier` (graph-selected) | `frontier_mech` (every linked name, no selection) | Does the graph's selection beat brute force? |
| `proportionality_shadow` | — | A live-data **rehearsal** of the real pipeline, bookkeeping only, that can never reach a real broker. |

Each paper lane trades a fake **$100,000**. If a smart lane can't out-score its
twin, the "smarts" are noise and get cut.

---

## 7. What we're actively testing — the falsifiers

Each strategy has a pre-set number that, if missed, **kills or disables it**
without touching the others:

| Strategy | The bar it must clear | Status |
|---|---|---|
| Ride (earnings drift) | Forward paper: ≥55% hit, positive after costs, calibrating | Running; July earnings season is the real test |
| Fade (crash overreaction) | First 40 fade signals: ≥55% reversion + positive after costs | Running; weak-but-honest so far |
| Frontier graph **quality** | A 50-edge sample must be ≥90% accurate | **PASSED at 96%** (after 3 tries: 64% → 88% → 96%) |
| Frontier **trading** | Per-bucket sector-neutral sign-accuracy ≥55% | **Not cleared** — coin-flip so far, and signal-starved |
| Phase 4 (the big one) | 8+ weeks racing; ≥1 bucket calibrated **and** beating its twin | Future |
| Before scaling real money | 60 days of *realized* profit | Future |

The point of a falsifier: we decided, in advance, the exact result that would
make us stop. That's what keeps hope from masquerading as evidence.

---

## 8. The phases (the roadmap, gate by gate)

| Phase | What gets built | The gate to advance |
|---|---|---|
| **0–1.5** ✅ | The three-mode machine, real data, risk re-enforcement | Plumbing proven; real earnings signal wired |
| **2 (ride)** ✅ gate passed | Earnings-drift strategy | Historical drift test passed against pre-set criteria |
| **2.5 (fade)** ✅ built | Crash-overreaction strategy + AI rubric | 40-signal forward falsifier (running) |
| **3 (Frontier)** | Supply-chain graph + propagation trading | Graph ≥90% (passed); per-bucket ≥55% (not yet) |
| **F (Lens)** | Flow Lens + Crumbs | F2 study answers "does crowd attention predict?" |
| **4 (the race)** | All strategies race in paper for 8+ weeks | ≥1 bucket calibrated and beating its twin |
| **5 (live)** | One walled-off account, real money | Dry-run clean → $50/trade → double only after 60 days of real profit |

We're at the seam of Phase 3 and the run-up to the **first live pilot**.

---

## 9. The schedules (what runs, and when)

**Daily automated jobs** (Windows Task Scheduler):

| Time | Job | What it does |
|---|---|---|
| 08:00 | Daily ingestion | Pull fresh prices/filings |
| 08:30 | Crumbs scan | Hunt odd-lot tender signals |
| 09:00 | Flow Lens | Log the simulated crowd |
| 17:00 | **Paper tick** | The main event: every strategy lane makes its decisions for the day on the shared fake-money book |
| at logon | Dashboard | Keeps the web view running |
| Mon 08:45 | RH auth check | Warns if the Robinhood login token is dying |

Two important timing facts:
- **The market isn't open 7 days a week, but the scheduler is.** A fix makes the
  paper tick **skip weekends and holidays** — otherwise it would "trade" stale
  prices and double-count crashes. (Juneteenth, Jun 19, falls inside the current
  shadow week and is correctly skipped.)
- **June is quiet; July is a flood.** Earnings season in July is when the ride
  strategy finally gets a real sample. Much of what we're doing now is getting
  ready for that.

**The near-term calendar:**
- **Now → ~Jun 22:** the **shadow week** — the live-data rehearsal lane must make
  the same decisions as the real lane with zero glitches.
- **~Jun 22/23:** reviewer judges the shadow week. If clean → you fund **$500**.
- **The pilot:** $500, $50/trade, first 10 orders manually approved, purpose is
  to **measure real-world slippage**, not to make money.
- **July:** earnings flood; ride + fade samples start to mean something.
- **Mid-July+:** Flow Lens has enough data to run its F2 study.
- **~8 weeks of racing → Phase 4 gate → only then any talk of scaling.**

---

## 10. The safety rails (why this can't blow up)

Even when we go live, the money is fenced in on every side:

- **Cash account, walled off.** Trading is pinned to a dedicated Robinhood
  **agentic cash account (••••0804)**, never your main account (••••2954). The
  code *refuses* to operate on any other account.
- **$50 per-order ceiling** and a **$500 total pilot cap** that is never topped
  up mid-pilot.
- **Manual approval** on the first 10 live orders — nothing places without you.
- **Kill switch.** Creating a file named `KILL_SWITCH` halts all trading
  instantly.
- **Simulated by default.** Going live is a deliberate one-line config flip,
  gated behind the shadow review — not something that happens by accident.
- **Point-in-time honesty** and **pre-registration** everywhere, so backtests
  can't cheat and results can't be massaged.

The live pilot's profit or loss **gates nothing** — scaling still requires the
strategy to pass calibration and the 8-week race and 60 days of real profit.
The pilot exists only to learn how much the real world differs from the
simulator.

---

## 11. Where we stand today (2026-06-13)

- **Ride** and **fade** strategies live in the daily paper race; **shadow week**
  rehearsing the live pipeline (Jun 15–18 + 22 trading days).
- **Frontier graph** passed its 96% quality bar; the **Frontier trading**
  strategy is built and its lanes are now **live in the race**, but it **has not
  shown an edge yet** (signal-starved, coin-flip so far). It also now tests a
  long-tradeable **up-side** (good news at a big partner → buy the small
  company), so it can accumulate forward data we could actually trade.
- **Robinhood is wired and proven.** Auth is done (token valid, account
  verified), the one broker bug is **fixed**, and the live read path is
  **verified against the real API** (reads $0 — the correct pre-funding state).
- **The $500 pilot is now push-button.** There's a written go-live runbook and a
  rehearsed manual-approval queue. Going live is a one-line config flip plus your
  approvals — **no more building required.**
- **What's left is waiting, not coding.** The next milestones are the shadow-week
  review (~Jun 22/23) and you funding the $500 — both judgment/calendar, not
  engineering. July earnings season is when the strategy samples get real.

---

## 12. Mini-glossary

- **Backtest / paper / live** — historical replay / fake-money-real-time / real money.
- **Lane** — one strategy's isolated fake-money account in the race.
- **Bucket** — a category of prediction that's scored and gated on its own (e.g.
  `earnings:ride`, `crash:fade`, `frontier:customer_concentration`).
- **Mech twin** — the dumb coin-flip control that every smart lane races.
- **Shadow lane** — a live-data rehearsal that books decisions but can't reach a
  real broker.
- **Calibration / Brier score** — how well stated probabilities match reality.
- **Sign-accuracy** — did it move the predicted direction (for Frontier, after
  removing the sector's move).
- **PIT (point-in-time)** — the rule that the system can't see the future.
- **Pre-registration** — writing the test and the pass/fail bar before seeing data.
- **Falsifier** — the pre-set result that would make us stop.
- **Slippage** — the gap between the price you expect and the price you get.
- **PEAD** — post-earnings-announcement drift (the ride strategy's basis).

# Two Engines and a Lens

An automated equity-trading **research system** built around one idea: *prove an
edge is real with fake money before it touches a real dollar, and kill any idea
that turns out to be luck.* The strategies are the easy part; the machinery that
stops the system from fooling itself is the point.

> **Status: paused (August 2026).** The daily paper race ran from June to early
> August 2026 and is stopped. The system has **never traded real money**. See
> [Where it stands](#where-it-stands) for honest results and known open issues.

Python ≥ 3.12 · ~23k lines of application code · ~12k lines of tests · 494 tests

---

## What it does

**One codebase, three modes.** Backtest, paper, and live run the *same* decision
code; only config changes. A point-in-time data feed makes look-ahead impossible
by construction, so a backtest can't see data the live system wouldn't have had.

**Two engines and a lens:**

| | Strategy | Idea |
|---|---|---|
| **Proportionality engine** | *Earnings ride* | Post-earnings drift: ride large positive surprises (top decile of an expanding history). |
| | *Crash fade* | Buy ≥10% one-day drops that an LLM judges to be an overreaction to the actual filing. |
| **Frontier engine** | *Supply-chain propagation* | Build a customer/supplier graph from SEC 10-K text; when an anchor company moves sharply, predict the move in its dependent filers. |
| **The Lens** | *Flow Lens, Crumbs* | Sensors only, no trading: crowd-attention logging across several LLM "personas", and an odd-lot tender-offer scanner. |

**LLMs judge; they never size.** Signals go through a cheap triage model, then a
two-model ensemble (Anthropic + OpenAI) with disagreement detection, then an
adversarial counter-pass that argues against the trade. Position sizing and risk
are purely mechanical and centrally enforced — the LLM layer cannot touch them.

## The discipline (the interesting part)

- **Brain vs. coin.** Every LLM-gated lane races a *control twin* that trades the
  same signals with no judgment. If the brain can't beat the coin, the brain adds
  nothing — the dashboard scores each strategy on that comparison.
- **Pre-registration.** Gate thresholds, falsifiers, sample sizes, and grading
  seeds are written into code *before* the data that judges them exists, then
  frozen. Example falsifier: the crash fade is disabled unless its first 40
  resolved signals revert ≥ 55% **and** are net-positive after costs.
- **Calibration as the capital gate.** A strategy bucket earns real capital only
  after ≥ 10 resolved predictions with a Brier score ≤ 0.25 — stated confidence
  has to match reality, not just the hit rate.
- **Shadow lane.** A lane that mirrors the gated strategy decision-for-decision on
  a simulated broker, used to certify the pipeline before any live order.
- **Live rails.** Broker pinned to a sandboxed, separately funded account (never
  the operator's main account), $50/order ceiling, manual approval for the first
  10 orders, and a file-based kill switch.

## Engineering highlights

- **Supply-chain edge extraction, held-out graded.** Regex guards → dedupe → an
  LLM validator with a frozen rubric. Each round was graded on a fresh, pre-seeded
  50-edge sample: **64% → 88% → 96%** precision before the trading code was allowed
  to use the graph.
- **Strategy-agnostic dashboard** (stdlib HTML, no framework): scoreboard per
  strategy, brain-vs-coin standing on bucket-attributed realized P/L, gate
  progress, per-event race tables, an AI-debate view of each LLM decision, and an
  ops-health panel.
- **Cost accounting.** Spend caps enforce *estimated* dollars that are deliberately
  biased high, reconciled monthly against provider dashboards.
- **Reproducibility.** Studies are re-run for bit-identical results; metrics are
  always recomputed from the persisted SQLite store, never from in-memory state.

### Bugs caught in review (a sample)

Each increment was specified in a handoff document with acceptance criteria
(`handoff-*.md`), built, then independently audited — re-running the suite,
re-deriving numbers from source data, and probing the live dashboard. That audit
loop caught, among others:

- **Phantom split gain.** A 1-for-4 reverse split turned a −1.9% trade into a
  recorded **+292%** (held share counts were never split-adjusted). In live
  trading, the exit would have tried to sell 4× the shares actually held.
- **False-green health check.** A crashed run still wrote "completed" run rows, so
  the ops panel showed a healthy tick for weeks while the race was stalled. Fixed
  in the run lifecycle *and* by requiring an actual portfolio snapshot as proof of
  work — a run row is not evidence of work.
- **Truncated model output.** A reasoning model spent its token budget thinking and
  returned cut-off JSON; caught by auditing a silent "ensemble unavailable" flag
  that fired on 95 of 115 calls.
- **Weekend replays.** Non-trading days let stale sessions re-detect Friday's
  crashes under fresh IDs, giving vetoed trades a second roll of the dice.

## Where it stands

**Backtest (earnings ride):** 174 trades, 60.9% hit rate, +5.67%/trade on the
primary 100-name universe; 839 trades, 62.0%, +5.80%/trade on a 1,376-name
secondary universe, positive every year. Caveats on record: survivorship bias in
the universe, and a Sharpe ratio distorted by cash drag.

**Paper race (June–August 2026):** directionally encouraging but **not
statistically meaningful** — resolved samples per strategy are in the single
digits, far below the pre-registered gates (10 for calibration, 40 for the fade
falsifier). No strategy has passed its gate; no capital was ever allocated.

**Known open issues before any live trading:**

- **Corporate actions:** held positions are not adjusted for stock splits (spec
  written in `handoff-corporate-actions.md`, not yet implemented). Signal detection
  is already safe — it uses split-adjusted history.
- The Frontier holding window resolves 4 elapsed trading days after entry, not
  the 5 its constant's name implies. Backtest and paper share the code, so results
  stay comparable; it's a naming issue, kept as-is for reproducibility.

## Repository layout

```
stockbot/
  bots/proportionality/   earnings ride, crash fade, quality gate, LLM rubric
  bots/frontier/          graph guards, LLM validator, dedupe, propagation strategy
  core/                   broker adapters + live rails, LLM router, risk, state store
  harness/                run loop, daily paper tick, trading calendar
  data/                   ingestion, point-in-time feed, universes
  research/               drift study, validations, Frontier backtest
  dashboard/              data layer + HTML app
  tools/                  operator CLIs (auth, approvals, fill reconciliation, scanners)
tests/                    494 tests
docs/                     overview, operator guide, go-live runbook, design notes
handoff-*.md              per-increment specs with acceptance criteria
implementation-plan.md    phased roadmap and gate definitions
```

Start with [`docs/project-overview.md`](docs/project-overview.md) for a
plain-language tour.

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (source .venv/bin/activate elsewhere)
pip install -e ".[dev]"
copy .env.example .env            # then fill in the API keys you have
pytest
```

The test suite needs no API keys or market data. Running the system for real
needs keys in `.env` (Tiingo for prices, an SEC contact user-agent, Anthropic and
OpenAI for the LLM layer):

```bash
python -m stockbot.cli backtest --mode backtest     # historical backtest
python -m stockbot.harness.paper_run                # one daily paper tick
python -m stockbot.dashboard.app --port 8765        # dashboard (reads data/paper.db)
```

Runtime state — the SQLite database, caches, logs, and generated reports — is
deliberately not committed.

---

*Research project. Nothing here is investment advice.*

# Shot 8 Handoff — Frontier Engine trading code (3c/3d): propagation strategy + paper race

End-to-end build brief, **shot 8 only**, for **one capable agent** (see "Why one agent" below). Reviewer audits at the end. Bar set by `reports/shot1..7-report.md`.

**Why this shot exists:** the seed-3005 precision sample PASSED at **96%** (`reports/3b-grading-5.md`) — the graph is finally trustworthy, so **3c/3d are UNFROZEN**. This shot builds the second engine: when a shock hits one company, trade the mechanically-linked names the graph says depend on it. It's been frozen since shot 2 — build it carefully and to the same pre-registration discipline as the ride/fade sides.

## Why one agent (not a model split)
The live strategy and the historical sign-accuracy measurement MUST share identical core semantics — the per-edge-type direction map, the PIT "edge live as-of date" selection, and the sector-neutralization. Splitting those across agents puts a seam exactly where the two halves must agree; a divergence would mean we *measure* one thing and *trade* another, invalidating the gate. The shot-6/7 splits worked because the pieces were genuinely independent (dashboard vs guards; regex vs LLM). This one isn't. One Opus-tier agent owns it end to end.

## Required reading
1. `reports/3b-grading-5.md` — the graph you're trading is the 106-named-edge survivor set; know its shape and the two known residual blemishes.
2. `implementation-plan.md` — the Phase 3 row (per-bucket ≥55% sector-neutral sign-accuracy; buckets advance or die independently), the shot-7 review block, conventions.
3. `stockbot/bots/proportionality/ride.py` + `fade.py` — the strategy/exit/ledger plumbing and the **control-twin** pattern. Reuse it; the Frontier engine is a sibling, not a fork.
4. `stockbot/harness/paper_run.py` — the additive, config-gated lane pattern (and the 5-R1 trading-calendar guard you must respect).
5. `stockbot/core/state/store.py` — `frontier_edges` schema + `get_frontier_edges(...)` (note the `filed_at` range variant for PIT).

## The graph, as it actually is (don't fight the data)
`frontier_edges`: `from_symbol` (the **filer** — the small-cap you'd trade), `counterparty_symbol` (resolved ticker of the big customer/supplier — the **anchor** whose shock you detect, **NULL ~70–83% of the time**), `counterparty_name`, `edge_type` ∈ {customer_concentration, sole_supplier, regulatory_precedent}, `magnitude_pct`, `quoted_passage`, `filed_at` (**edges are live only `filed_at ≤ date ≤ filed_at + 18 months`** — PIT is in the schema; honor it).

**The hard constraint that shapes the whole shot:** a price-shock can only propagate from a counterparty you can *observe*, i.e. `counterparty_symbol IS NOT NULL`. Only that subset of the 106 named edges is tradeable by price propagation. Expect this to be small.

## Pre-register IN CODE before any backtest (hard rule, like 2b/2c/2.5b)
Freeze these as constants/maps before you score one day of history. If you tune them after seeing results, STOP and write it down.

- **Anchor-shock definition:** a threshold move on `counterparty_symbol` on day T (reuse the crash-detector mechanism; pick a threshold and DIRECTION policy — down-only vs bidirectional — and pre-register it). Report the resulting signal frequency (ticket F-0) before committing.
- **Direction map (the falsifiable hypothesis), per edge type:**
  - `customer_concentration` (anchor = the filer's big *customer*): a negative shock to the customer threatens the filer's demand → predict **filer DOWN**.
  - `sole_supplier` (anchor = the filer's sole *supplier*): a negative shock to the supplier threatens the filer's supply → predict **filer DOWN**.
  - `regulatory_precedent`: **OUT of scope for price-anchor trading** — you cannot price-shock-anchor on "the EPA"; the anchor is a peer regulatory event, a different mechanism. Build it MEASURE-ONLY (report what a peer-precedent signal would look like) or defer it cleanly to a later shot. Do NOT force a junk lane.
- **Holding window** (e.g. enter T+1, hold N trading days — pre-register N), **position size** (small, ≤3% equity, max-concurrent cap — mirror the fade side), and **sector-neutralization method** (filer's return minus a pre-registered sector benchmark/peer proxy over the window; sign-accuracy = sign(excess return) matches the predicted direction).
- **Buckets:** `CalibrationLedger.strategy_bucket("frontier","customer_concentration")` and `("frontier","sole_supplier")` — separate buckets that advance or die independently (Phase 3 rule).

## Ticket F-0 — signal-frequency reality check (FIRST; report before building the strategy)
Before the full build, answer from the real historical dataset + the live graph: how many PIT-valid, observable-counterparty propagation signals exist per year, per bucket? (We hit this wall before — the 100-name universe gave <1 ride signal/week, which is why U1 expanded it.) If the tradeable set is too thin to ever reach a meaningful sample, we want to know NOW and decide (widen the anchor threshold, add counterparty resolution, or scope down) rather than after building a race that never fills. Put the number in the report plainly.

## Ticket 3c — propagation strategy + PIT-correct sign-accuracy measurement
- `FrontierPropagationStrategy` (sibling of the proportionality strategy): on each tick, detect anchor shocks on resolved counterparties, look up the PIT-valid edges (`filed_at ≤ now ≤ filed_at+18mo`) pointing from those counterparties, and emit predicted-direction entries on the filer names. LLM never sizes or selects — this is mechanical propagation off a pre-verified graph. Risk-gate every order through `StrategyContext.submit_and_record` (BUYs gated; never weaken RiskEngine/PIT). Pre-registered exits (hard stop, time-stop, the holding window).
- **Measurement (the gate is judged on this + forward paper, not a build-time pass/fail):** a PIT-correct historical backtest reporting **per-bucket sector-neutral sign-accuracy, with n**, against the **≥55%** bar. Use only edges live as-of each trade date; neutralize sector; report customer_concentration and sole_supplier separately. Weak-but-honest is reportable; silence is not. Note survivorship/coverage caveats as the drift studies did.

## Ticket 3d — paper-race lanes + control twin (additive, config-gated)
- Add Frontier lanes to the daily tick the same way the fade side did: a gated `frontier` lane (graph-driven propagation) **plus a mechanical control twin** (`frontier_mech`: trades EVERY observable anchor shock on EVERY PIT-valid linked name, no selection, constant probability, same exits) so the graph's value is measured brain-vs-coin. Config-gated (default OFF), one process, shared feed cursor, **do not disturb the ride/fade/shadow lanes or their semantics**. Respect the 5-R1 trading-calendar skip.
- Calibration ledger records real predictions under the two `frontier:*` buckets; the per-bucket ≥55% sign-accuracy is the race-time falsifier (like the fade side's 40-signal rule) — a bucket that can't clear it is disabled without touching the others or the other engines.
- Dashboard lanes/calibration rows appear automatically (4b-3 enumerates buckets dynamically); confirm the `frontier:*` rows render.

## Ticket R1 (riders, small, optional — non-blocking)
- gpt-5.5 price row: still deferred to the user's settled July OpenAI reading. Leave `router.py` as-is.
- Two carry-forwards from the shot-7 grade (only if quick; not gating): tighten the validator's magnitude check for "% of **{segment}'s** revenue" phrasing (the AIN/Safran miss), and make dedupe tolerant of a 1-char counterparty typo (the AQST/Indivior duplicate). These touch `validator.py`/`dedupe.py`, not the trading code — keep them isolated.

## Hard rules
1. STOP after the Frontier lanes are live in paper and the sign-accuracy measurement is reported. No 3e/3f (ADR-lag), no live broker, no Phase 5, no real orders, no funding.
2. Ride/fade/shadow lanes, exits, RiskEngine, PIT, the feed cursor, and every existing test untouched. New lanes additive and config-gated; one tick process.
3. Pre-registration: anchor definition, direction map, holding window, sizing, sector method, and bucket labels frozen in code before the backtest is scored.
4. PIT is law: only edges with `filed_at ≤ trade_date ≤ filed_at+18mo`; no future graph leak. The strategy must never see an edge before its filing date.
5. LLM: none required at runtime for propagation (mechanical). If F-0 or anything tempts an LLM call, it's out of scope — flag it, don't add it.
6. Honesty: signal-frequency reality (even if disappointing), per-bucket n and sign-accuracy (even if weak), coverage/survivorship caveats — all IN the report.

## Completion report (`reports/shot8-report.md`)
Files+tests (before/after + suite tail); F-0 signal-frequency table (per bucket, per year, observable-counterparty count); the pre-registered direction map + constants verbatim; per-bucket sector-neutral sign-accuracy + n vs the ≥55% bar (PIT-correct backtest); 3d lane evidence (scratch-tick brain-vs-coin split, like the fade demo) + dashboard `frontier:*` rows; regulatory_precedent disposition (measure-only/deferred, stated plainly); rider status; deviations; open questions.

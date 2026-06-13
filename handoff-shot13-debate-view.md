# Shot 13 Handoff — AI "debate view" on the dashboard (read-only; feeds prompt tuning)

End-to-end build brief, **one ticket, one cheap-to-mid agent**. Read-only dashboard work; same hard rules as 4b-3/shot-10 (additive, **no new write paths**, stdlib HTML only, no JS frameworks). Reviewer audits. **Lower priority** than the go-live/fill work — this is an inspection/insight feature, not a gate.

**Why:** the full AI exchange behind every fade decision is **already persisted** (no new capture needed) but the dashboard only shows a one-line rationale + flags. Surfacing the whole debate lets the owner (and the reviewer) *see how the discourse plays out* — which directly informs tuning the counter-pass prompt (see the fade-v2 work, `handoff-shot11-fade-v2.md`).

## What's already stored (confirmed — just read it)
Each fade decision row's `inputs.fade_gate` JSON contains the full `counter_pass` dict + rubric detail:
- `counter_pass`: `argument` (the devil's-advocate case, ≤200 words), `strongest_point`, `reread_rationale`, `revised_probability`, `final_probability`, `vetoed`, `counter_model_id`, `reread_model_id`.
- Rubric ensemble: `model_probabilities` and `model_classifications` (per-model: who said what + their probability — i.e. where the two models agreed/disagreed), `ensemble_probability`, `rationale`, `flags` (incl. `ensemble_disagreement`, `counter_pass_veto`, etc.).
For the **ride** side, the quality-gate `rationale`, `triage`, `flags`, and `quality_score` are likewise stored.

Read `stockbot/bots/proportionality/fade.py` (the `FadeAssessment`/`counter_pass` shape) and `stockbot/bots/proportionality/ride.py` (how `fade_gate`/`quality` land in the decision `inputs`) to map the fields. Read `stockbot/dashboard/data.py` `thesis_detail` + `_entry_quality` — extend these read paths; don't change what's written.

## Build — a "debate" drill-down
On the existing thesis drill-down (`thesis_detail`), add a clearly-laid-out **AI Debate** section that reconstructs the exchange for that thesis, in plain reading order:

1. **The rubric round** — for each model (Opus, GPT): its classification (overreaction / proportionate / underreaction) and its probability, **side by side**, with a clear marker when they **disagreed** (the `ensemble_disagreement` flag / probability gap) and a note that we kept the lower number.
2. **The devil's-advocate argument** — render `argument` and `strongest_point` as a readable quote block ("The case that the crash is CORRECT:").
3. **The re-read & verdict** — the primary model's `reread_rationale`, the `revised_probability`, and the **final call**: entered, or **VETOED** (with `final_probability` vs the 0.55 threshold shown). Make veto vs. entry visually obvious.
4. **Provenance** — which model argued vs. re-read (`counter_model_id` / `reread_model_id`), and the `prompt_version` (so when fade-v2 lands, you can tell v1 vs v2 debates apart).

Keep it dense and readable (the owner likes data on screen). Plain language for any jargon, sourced from `glossary.py` where a term needs it. Handle gracefully: ride theses (no counter-pass — show the quality rationale instead), degraded runs (`ensemble_unavailable` — show "GPT was unavailable; Claude-only, capped 0.60"), and old rows missing fields.

## Optional (nice, if cheap): a "debates" index
A small list view of recent fade decisions with: symbol, entered/vetoed, the two models' agree/disagree, and a link into the debate drill-down — so the owner can skim "how are the arguments shaping up" without clicking each. Read-only, newest first.

---

## Insights panels (added by owner request) — BUILD IN PRIORITY ORDER, stop if it's getting too big
All read-only over already-stored data. **Do the debate view + Tier 1 first; only continue down the tiers if there's clean headroom.** A partial, polished delivery beats a sprawling one — say in the report where you stopped.

### Tier 1 — Gate scoreboard ("are we there yet?") — highest value
One compact panel showing each strategy's progress toward its kill-or-advance bar, with honest sample-size context. Pull from the calibration ledger + fills + decisions:
- **Ride** (`earnings:ride`): resolved predictions k / 10 needed, current Brier vs the ≤0.25 bar.
- **Fade** (`crash:fade`): signals so far X / 40 (the falsifier), reversion hit-rate, net return after costs.
- **Frontier** (`frontier:customer_concentration`, `frontier:sole_supplier`, + `_up`): per-bucket n and sector-neutral sign-accuracy vs the 55% bar.
- **Each brain-vs-coin race** (gated vs mech; v1 vs v2 vs `crash_fade_mech`): cumulative edge with a plain tag — `too early (n=…)` until the sample is meaningful, else `meaningful`.
Make "passing / failing / not enough data yet" obvious at a glance. This is the project's missing "scoreboard."

### Tier 2 — AI-value trackers (pairs with the debate view; informs v2)
- **Model disagreement:** how often Opus vs GPT split (the `ensemble_disagreement` flag / probability gap), the average gap, and — once theses resolve — who was right more often when they disagreed. (Uses the stored `model_probabilities`/`model_classifications` + outcomes.)
- **Veto-value tally:** of fades the counter-pass VETOED, what fraction would have won vs lost over the hold window (from the logged `counter_pass` + the thesis outcome). Shows whether the devil's-advocate adds value or destroys it. Split by `prompt_version` so v1 vs v2 are comparable. Will read "not enough resolved vetoes yet" until ~July — render that honestly.

### Tier 3 — Thesis-validation (stretch; only if Tiers 1–2 are clean)
- **Exit attribution:** for closed trades, the exit reason (profit target / trailing / hard stop / time-stop) and P&L grouped by reason — is an exit rule leaking? (Cheap: the exit `action` is on the decision/fill rows.)
- **Drift curve (optional within Tier 3):** average price path from entry across the hold window for resolved ride trades — does post-earnings drift actually materialize? More involved (needs per-day marks); skip if it balloons scope.

### Deferred — execution quality (do NOT build here)
The realized-vs-expected **slippage / overnight-gap** view needs the live-fill data that **shot 12** captures. Note it as "blocked on shot 12," do not attempt it.

## Hard rules
1. Read-only; no new write paths; the dashboard stays a pure view over the DB.
2. Additive only — don't change existing panels' data, the thesis browser, or any written field. Don't collide with in-flight shot-10 dashboard changes (work in `data.py` read helpers + a new detail section).
3. Never modify a non-dashboard module or an existing test.

## Acceptance
- The thesis drill-down shows the full debate for a fade thesis (rubric round → argument → re-read → verdict → provenance), legibly, sourced from already-stored data.
- **Tier 1 gate scoreboard renders** with per-strategy progress + honest sample-size tags (at minimum this, beyond the debate view). Tiers 2–3 as headroom allows — the report states where you stopped and why.
- Ride theses and degraded/old rows render without errors (graceful fallbacks); every insight panel handles the empty/"not enough data yet" case explicitly (most buckets are near-empty today — they must read sensibly with n=0).
- New tests (`tests/test_shot13_debate_view.py`): reconstruct a debate (entered AND vetoed cases); the gate scoreboard renders with both empty and populated ledgers; any Tier-2/3 panel built has a zero-data test; missing-field rows don't crash.
- Existing dashboard tests green; dashboard restarted + verified at `http://127.0.0.1:8765`.

## Completion report (`reports/shot13-report.md`)
Files+tests; the fields surfaced; a text/HTML snippet of one entered and one vetoed debate; how it degrades on ride/old/degraded rows; note that this is the inspection surface for tuning fade-v2 prompts; deviations.

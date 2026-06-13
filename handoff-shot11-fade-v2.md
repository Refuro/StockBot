# Shot 11 Handoff — Fade counter-pass v2 (anti-sycophancy), as a parallel champion/challenger

End-to-end build brief, **shot 11 only**, for **one capable agent** (LLM-prompt design + a careful shared-ensemble refactor; small but needs care). Reviewer audits. Bar set by `reports/shot1..9-report.md`.

**Why this shot exists:** the fade side's adversarial counter-pass (a model argues "the crash is correct," the primary re-reads and may veto the fade) may be undermined by **sycophancy** — the re-reading model could lower its number just to be agreeable, not because it genuinely reconsidered. v2 rewrites that prompt to force real engagement, and **runs alongside v1 as a head-to-head** so the forward race tells us which counter-pass is better (and whether either beats the coin). v1 is the running champion — **do not touch it**; v2 is purely additive.

## Required reading
1. `stockbot/bots/proportionality/fade.py` — `_COUNTER_SYSTEM`, `_REREAD_SYSTEM`, `_run_counter_pass`, `assess_crash_event`, `CrashFadeGate`, `PROMPT_VERSION = "fade-v1"`, the veto rule `final = min(ensemble, revised)`.
2. `stockbot/bots/proportionality/ride.py` + `stockbot/harness/paper_run.py` — the fade lane wiring and the additive, config-gated **control-twin** pattern (the fade side + `crash_fade_mech`; and how shot 9 added the frontier lanes). Mirror it.
3. `reports/shot5-report.md` (2.5b) — the pre-registered fade design and the 40-signal falsifier that is **currently accumulating on fade-v1** (must not be disturbed).

## The three prompt changes (the substance of v2)
Rewrite ONLY the counter-pass stage (the rubric ensemble is unchanged). New constants `_COUNTER_SYSTEM_V2` / `_REREAD_SYSTEM_V2`, `PROMPT_VERSION_V2 = "fade-v2"`:

1. **Blind the re-read.** v1's `_REREAD_SYSTEM` shows the model its own prior ("YOUR ORIGINAL ASSESSMENT: …probability=0.62…") — an anchor begging to be nudged. v2 must **not** reveal the prior probability; the model re-derives without seeing its earlier number.
2. **Engagement before the number.** Require the re-read to first state, in its JSON, the counter-argument's **strongest point** AND its **best rebuttal**, and only then the probability — so it has to reason, not just agree. (Add the two fields to the parsed JSON; keep parsing defensive.)
3. **Re-derive, don't "revise."** Phrase it as "state your probability **from scratch** given all the arguments," not "revise your estimate." Removes the small-nudge anchoring.

Everything else stays identical: the veto rule `final = min(ensemble_probability, revised)`, the 0.55 threshold, fail-closed on unparseable/error, the counter-pass result logged on EVERY decision.

## The architecture — share the ensemble, fork only the counter-pass
The expensive part (Haiku triage + Opus+GPT rubric ensemble) **must run ONCE per crash and be shared** between v1 and v2 — only the counter-pass differs, so the marginal LLM cost is just v2's ~2 counter-pass calls on the rare candidates that clear the entry gate.

- Refactor `assess_crash_event` so the rubric-ensemble result (stages 1–3) is computed once and the **counter-pass (stage 4) is parameterized by version** (v1 vs v2), producing two `FadeAssessment`s from one ensemble run. Do NOT re-run the ensemble per version.
- A gate that, for a given crash, returns both the v1 and v2 assessments (or a `version`-keyed call sharing the per-event ensemble cache). Verify by test that the ensemble/triage calls happen once regardless of how many counter-pass versions run.

## The lane — `crash_fade_v2`, racing v1 and the coin
- Add a parallel fade lane `crash_fade_v2` (config-gated, additive — mirror `crash_fade_mech`): same crash detection, same exits, same sizing, but it enters on **v2's** veto decision and books to bucket **`CalibrationLedger.strategy_bucket("crash","fade_v2")` = `crash:fade_v2`**, `prompt_version="fade-v2"`.
- The existing gated lane keeps **v1** (`crash:fade`) — bit-identical. `crash_fade_mech` stays the coin baseline. Now it's a clean **three-way**: v1 (brain) vs v2 (smarter brain) vs mech (coin), all on the same crashes.
- **Enable it in `config.yaml`** so it starts accumulating now (like shot 9d enabled frontier). Default OFF in code; ON in config.

## Hard rules
1. **Do NOT modify fade-v1** — its prompts, threshold, veto rule, bucket, or the 40-signal falsifier accumulating on it. v2 is additive only; the existing fade race must be bit-identical (prove it with a "v1 unchanged when v2 disabled" test and the unchanged suite).
2. Don't touch ride/frontier/shadow lanes, RiskEngine, PIT, the broker, or any existing test.
3. **Pre-register** the v2 prompt constants + bucket + threshold in code BEFORE any scoring; nothing tuned after seeing results.
4. LLM spend stays inside the existing $5/day tick cap; the rubric ensemble is shared (no double ensemble spend) — verify by test/log. The counter-pass result is logged on every v2 decision (entered or vetoed), same as v1.
5. Honesty: report that the v1-vs-v2 verdict is **slow** (fade signals are thin) — this shot stands up the experiment; it does not claim a winner.

## Acceptance
- v2 prompt constants frozen/pre-registered; the v2 re-read prompt provably **omits the prior probability** and **requires strongest-point + rebuttal before the number** (assert on the constructed prompt + parsed JSON).
- Shared-ensemble proven: one crash → triage+rubric ensemble called once → two assessments (v1, v2); a spend/cache test shows no double ensemble spend.
- `crash_fade_v2` lane live and config-enabled, booking `crash:fade_v2` with the counter-pass logged every decision; v1 fade race bit-identical (test + unchanged suite count for existing tests).
- Dashboard surfaces the `crash:fade_v2` bucket/lane (dynamic enumeration — no dashboard code change needed; confirm it appears).
- Offline end-to-end test: synthetic crash → shared ensemble → v1 keeps/vetoes per v1 prompt, v2 keeps/vetoes per v2 prompt, both independently logged. Full suite green.

## Completion report (`reports/shot11-report.md`)
Files+tests (before/after + suite tail); the v2 prompts verbatim + the three changes; proof the ensemble is shared (call counts/spend); the v2 lane + `crash:fade_v2` bucket evidence (scratch tick) + dashboard row; confirmation v1 is bit-identical; deviations; open questions. State plainly that the forward v1-vs-v2-vs-coin comparison accrues over months (thin signals) — this shot only stands it up.

## Explicitly NOT in scope
- The **veto-value analysis** (does the counter-pass add value at all — measured from logged vetoes, ~July when fades resolve). That's a separate reviewer analysis, not this build.
- Any change to v1, to go-live prep, or to order types (marketable-limit idea stays backlog).

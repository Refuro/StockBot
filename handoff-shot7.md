# Shot 7 Handoff — Edge precision, round 5: sharper regex guards + a semantic LLM validator

End-to-end build brief, **shot 7 only**, run by **two parallel agents** (model split below). Reviewer audits at the end. Bar set by `reports/shot1..6-report.md`.

**Why this shot exists:** the seed-3004 sample graded **44/50 = 88%** (`reports/3b-grading-4.md`) — one edge under the ≥90% bar, up from 64% in round 3. We are NOT abandoning the graph. Two complementary fixes, belt-and-suspenders:
- **More regex, with nuance** (Agent A) — the round-4 failures are subtle surface forms the current patterns just missed; tighten them.
- **A cheap semantic LLM validator** (Agent B) — for the failures a regex fundamentally can't judge (hypothetical-vs-actual language; a product where a supplier is expected). Haiku, one call per candidate edge, frozen rubric.

Regex stays the **free pre-filter**; the LLM validator is the **precision layer on top**. Both must be in place before the re-filter and the new sample.

## The model split (parallel, hard file boundaries)
- **Agent A — `handoff-shot7-agentA-regex.md`** → cheap-to-mid model. Pure offline pattern work: new/extended guards targeting the 6 round-4 failures, with the round-3 AND round-4 graded edges as frozen fixtures. No LLM, no network.
- **Agent B — `handoff-shot7-agentB-llm-validator.md`** → **capable model (Opus-tier).** The nuanced lane: design the frozen-rubric Haiku edge-validator, wire it as the precision layer, fix insert-time dedupe, then run the combined pipeline and draw the seed-3005 sample. This is where prompt-design judgment and cost discipline live — worth the spend; the work is bounded.

They can build in parallel against fixtures/stubs. **The final combined re-filter + seed-3005 sample is Agent B's last step and runs only after Agent A's guards are merged and green** (same gating pattern as shot 6's re-filter).

## Required reading (both agents)
1. `reports/3b-grading-4.md` — the round-4 failure taxonomy IS the spec.
2. `reports/3b-grading-3.md` — round-3 failures (still must stay dead).
3. `reports/3b-precision-sample-4.md` and `-3.md` — the graded quotes = your labeled fixture set (50 + 50).
4. The existing guard module (`stockbot/bots/frontier/guards.py`, rounds 1–3) and `stockbot/research/refilter_graph.py`.

## Ticket R1 (riders) — reviewer-managed, do NOT redo
- gpt-5.5 price row: still deferred to the user's settled July OpenAI reading. Leave `router.py`'s `(7.00, 40.00)` as-is.
- Four-lane fade tick confirmation: a future reviewer check at shadow-week review (~Jun 22/23). Touch nothing.

## Hard rules
1. STOP after `reports/3b-precision-sample-5.md` exists (seed 3005, ungraded). No 3c/3d trading code, no Phase 5, no real orders, no funding — Frontier stays FROZEN until a sample clears ≥90%.
2. Race/shadow/fade lanes, exits, risk, PIT, and every existing test untouched (suite is at 280 — never modify an existing test).
3. **Pre-registration is law.** Regex patterns AND the LLM rubric are frozen in code BEFORE the full-graph re-filter runs. The 50+50 labeled edges are a DEV CHECK only — you may NOT tune patterns/rubric to overfit them, and you may NOT look at or tune against the held-out seed-3005 survivors. If you change anything after seeing full-graph output, STOP and write it down as a deviation.
4. LLM spend ≤ **$3 total** this shot (validator is Haiku; cache by edge-id; token-logged). Estimates high, never knowingly low.
5. Honesty: every collateral kill (good edges lost to a new guard or the validator), recall/precision trade-off, and surprise goes IN the report.

## Acceptance (combined, measured by the reviewer)
- Agent A fixtures green: all 6 round-4 failures + all 18 round-3 failures killed/demoted by a pre-registered guard; ≥ the retained-correct floors hold (don't regress round-3's 31/32 or round-4's correct edges beyond a stated, justified collateral budget).
- Agent B: the LLM validator, on the 50 round-4 labeled edges as a held-back dev check, agrees with the reviewer's verdicts (kills the 6, keeps most of the 44) — report its confusion matrix honestly.
- `reports/3b-precision-sample-5.md` exists (seed 3005, 50 named survivors, ungraded). The reviewer grades it against ≥90%; shot 8 unfreezes 3c/3d on a pass.

## Completion report (`reports/shot7-report.md`)
Files+tests (before/after + suite tail); Agent A fixture results (round-3 + round-4 kills by guard, collateral); Agent B validator design + frozen rubric + dev-check confusion matrix + per-edge validator log location; combined re-filter table (regex kills, LLM kills, dedupe merges, edges/named before→after); dedupe fix evidence; confirmation `3b-precision-sample-5.md` exists; LLM spend (token-logged); deviations; open questions. (If two agents write, each owns its own clearly-headed section — don't overwrite the other's.)

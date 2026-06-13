# Shot 6 — Agent B brief: finish the guards, then re-filter + re-sample (Tickets 3a-H → 3c-prep-2)

**You are one of two parallel build agents on shot 6. You own the integrity path: 3a-H then 3c-prep-2, in that order (hard dependency).** The other agent owns the read-only dashboard and won't touch your files. A cheap-to-mid model is fine; escalate model only if the guard-attribution fix below resists one focused pass.

**Context:** the previous builder (now offline) already did the hard part — designed all 7 round-3 guards in `stockbot/bots/frontier/guards.py` (~731 lines) and encoded the 50 graded edges as frozen fixtures in `tests/test_shot6_3aH_guards.py`. **10 of 11 guard tests pass.** Your job is to land the last one, then run the re-filter and re-sample. Do NOT redesign the guards; finish and apply them.

## Required reading
1. `reports/3b-grading-3.md` — the failure taxonomy; the 50 verdicts are the spec.
2. `reports/3b-precision-sample-3.md` — the graded quotes.
3. The full handoff `handoff-shot6.md` — tickets 3a-H and 3c-prep-2, and the hard rules.
4. The existing round-1/2 guards (3a-F / 3a-G) that the round-3 module extends.

## Step 1 — finish 3a-H (one failing test)
`tests/test_shot6_3aH_guards.py::test_all_18_incorrect_edges_leave_named_pool` fails on a **guard-attribution** mismatch: an edge that fails for two reasons at once is being claimed by the wrong guard relative to the pre-registered `EXPECTED_GUARDS` map (an edge expected to die by `g5_denominator` is being killed first by `g1_internal_entity`, or vice-versa). The 18 INCORRECT edges all still leave the named pool — the **count** is right; only **which guard gets credit** is off for one dual-failure edge.

Fix by reconciling **guard precedence** in `apply_edge_guard_stack` with the pre-registered attribution, NOT by editing the fixtures or the `EXPECTED_GUARDS`/`EXPECTED_COLLATERAL` maps (those are frozen — they encode the reviewer's grade). Decide the correct precedence order so each dual-failure edge is attributed to the guard the grading report assigns it, then make the stack run in that order. Keep all 10 currently-passing assertions green.

**Pre-registration rule (critical):** the guards must be frozen *before* you run the re-filter in step 2. If you find yourself tweaking a pattern after seeing re-filter output on real (non-fixture) edges, STOP and write it down in the report instead — that's a documented deviation, not a silent edit.

**3a-H acceptance:** `tests/test_shot6_3aH_guards.py` fully green — all 18 INCORRECT edges killed/demoted by their pre-registered guard, ≥30 of 32 CORRECT retained (the fixtures assert exactly 31 retained + collateral `{29: g6_stale_year}`).

## Step 2 — 3c-prep-2 (re-filter + re-sample), only after step 1 is green
- Apply the **full guard stack (rounds 1–3)** to **all 373 edges** in `data/state.db`. Quotes are retained on every edge, so this is mostly a metadata pass; re-fetch cached EDGAR doc text only where a guard needs more context (cached → ~free). **LLM use only if genuinely unavoidable, ≤ $5 Haiku cap, token-logged.**
- Produce stats: edges before/after, kills by guard name, surviving counts by type, named/anonymous split before→after (G3 demotes some named → anonymous).
- Regenerate the precision sample: **50 named edges, seed 3004** (pre-registered in `reports/3b-grading-3.md` — use exactly that seed) → `reports/3b-precision-sample-4.md`, same format as `-sample-3.md` (numbered edges, quote, magnitude, filing ref, blank verdict checkboxes). If fewer than 50 named edges survive, sample ALL survivors and say so plainly.
- **Do NOT grade it. Do NOT build 3c/3d trading code.** The reviewer grades it against the ≥90% bar; shot 7 unfreezes only on a pass.

## Hard rules (yours)
1. STOP after `3b-precision-sample-4.md` exists. No 3c/3d, no Phase 5, no real orders.
2. Don't touch the dashboard files (the other agent owns them), the race/shadow/fade lanes, exits, risk, PIT, or any existing test.
3. Guards frozen-green before re-filter; fixtures and EXPECTED_* maps are frozen.
4. ≤ $5 total LLM this shot, token-logged.
5. Honesty: report every collateral kill by name+guard, any band gaps, anything surprising — in the report, not around it.

## Report
Own `reports/shot6-report.md` (the dashboard agent appends only its own `### 4b-3` section). Include: files + test count before/after + suite tail; 3a-H fixture results (18/18 killed by correct guard, 31/32 retained, collateral list); the precedence fix you made and why; re-filter table (kills by guard, edges/named before→after, by type); confirmation `3b-precision-sample-4.md` exists (seed 3004, ungraded); deviations; open questions.

## Note on R1
R1 is already closed by the reviewer (fade_demo deleted, gpt-5.5 row deferred to the user's July reading, tick-confirmation is a future reviewer check). Do not redo it.

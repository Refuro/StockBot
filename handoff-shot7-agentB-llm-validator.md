# Shot 7 — Agent B brief: semantic LLM edge-validator + dedupe + combined re-filter + seed-3005 sample

**You are one of two parallel agents on shot 7. You own the semantic layer and the integration.** This is the nuanced lane — prompt design, determinism, cost control, pipeline wiring — so it's assigned a **capable model**. Agent A owns the regex guards (offline); you wire your validator AFTER the regex pre-filter and run the final combined pipeline once Agent A's guards are merged.

## Required reading
1. `reports/3b-grading-4.md` and `-3.md` — the reviewer's verdicts and reasoning. Your rubric must reproduce this judgment.
2. `reports/3b-precision-sample-4.md` + `-3.md` — the 50+50 labeled edges = your **held-back dev check** (NOT a training set).
3. `stockbot/research/refilter_graph.py` — the re-filter you extend.
4. `stockbot/core/llm/router.py` — the router/SpendTracker; the 2c-F1 JSON-mode/headroom handling. Reuse it; do not weaken caps.
5. `stockbot/bots/proportionality/fade.py` — a worked example of a frozen-rubric, JSON-output, spend-capped LLM gate. Mirror its discipline.

## Part 1 — the edge validator (the point of the shot)
A `FrontierEdgeValidator` that takes ONE candidate edge **that already survived the regex pre-filter** and asks Haiku a single, frozen-rubric question: does this edge's quote genuinely support the typed relationship? This is the semantic layer for failures a regex can't judge (hypothetical-vs-actual language; product-vs-supplier; subtle direction).

- **Model:** Haiku only (cheap; this is a filter, not a grader). Temperature 0, JSON mode, cache by `edge_id` so re-runs don't re-spend.
- **Input to the prompt:** edge type, source ticker, counterparty string, claimed magnitude (or n/a), and the stored quote (truncate to a sane char budget).
- **Frozen rubric — ALL must hold or the edge is invalid** (pre-register these in code as the system prompt constant, before running on the full graph):
  1. The typed relationship is **actually asserted**, not hypothetical/risk-factor ("may", "could", "frequently includes", "for example" with no concrete action) — especially for `regulatory_precedent`, require a real attributable action.
  2. **Direction** (sole_supplier): the SOURCE is the dependent buyer and the COUNTERPARTY is the supplier — NOT the filer serving as supplier to the counterparty.
  3. The counterparty is an **external named entity** — not a product, an industry/sector/vertical, the filer's own segment/plant/system, a geography, or a legal instrument.
  4. **Magnitude** (when present): the number is the **latest period** and a **company-level** denominator (total/consolidated/net sales), not a prior year or a segment/subsidiary denominator.
  5. The relationship is **current**, not terminated/historical ("did not renew", "historically", "we have moved").
- **Output JSON:** `{"valid": true|false, "failed_check": <1-5 or null>, "reason": "<short>"}`. Parse defensively (reuse `parse_json_object`); an unparseable or errored validation **fails closed** (edge dropped) and is flagged — never silently kept.
- **Spend:** the validator runs only on regex survivors; cap at **$3** total this shot via the router's SpendTracker, token-logged. Estimate the run first (≈ candidate count × Haiku per-call) and report it.

## Part 2 — dedupe fix (insert-time)
Round-4 grading found duplicate edges inflating the named pool: **EVTC** (Popular vs its subsidiary Banco Popular, same 29%), **GLIBA/GLIBK** (dual share classes, identical quote), **CENT/Home Depot** (identical passage). Add dedupe so the graph and the precision pool don't carry duplicates:
- Collapse edges sharing (normalized source, normalized counterparty, type), and edges sharing (source, type, near-identical quote).
- For dual-class tickers (same CIK, e.g. GLIBA/GLIBK) and parent/subsidiary counterparties, keep one canonical edge; record the merge.
- Report every merge; this changes edge counts, so it must be auditable.

## Part 3 — combined re-filter + seed-3005 sample (your LAST step; runs after Agent A merges)
Order of operations on the full graph, once Agent A's regex guards are green and merged:
1. regex pre-filter (rounds 1–4) — free, kills the easy cases;
2. dedupe;
3. LLM validator on the survivors — the semantic precision layer;
4. write `reports/3c-prep-2-refilter-stats-v2.json` (or similar): edges before/after each stage, regex kills by guard, dedupe merges, LLM kills by `failed_check`, named/anonymous before→after, surviving counts by type;
5. regenerate the precision sample: **50 named survivors, seed 3005** (pre-registered in `reports/3b-grading-4.md`) → `reports/3b-precision-sample-5.md`, same format as `-sample-4.md`. If < 50 named survive, sample ALL and say so.
6. **Do NOT grade it. Do NOT build 3c/3d.** The reviewer grades against ≥90%.

## Anti-overfit / honesty (critical — this is the whole credibility of the approach)
- Freeze the rubric in code BEFORE running on the full graph. The 50+50 labeled edges are a **dev check**: run the validator on them, report a confusion matrix vs the reviewer's verdicts (did it kill the 6 round-4 failures? how many of the 44 correct did it wrongly kill?). You may inspect that matrix to confirm the rubric is sane, but you may **not** iterate the rubric to chase those specific edges, and you may **not** look at or tune against the seed-3005 survivors. If you revise the rubric after seeing dev-check results, that's one documented deviation, then re-freeze — don't loop.
- Log every validator decision per edge (edge_id, valid, failed_check, reason, token cost) to a file the reviewer can audit. The validator filters; it never grades the held-out sample — that's the reviewer's job.

## Acceptance
- Validator unit-tested with stubbed Haiku responses (deterministic): each of the 5 checks fires on a crafted edge; fail-closed on unparseable; cache prevents double-spend.
- Dev-check confusion matrix reported honestly (the bar is "kills the 6, keeps most of the 44"; perfect is not required — the regex layer also contributes).
- Dedupe merges reported and tested.
- `reports/3b-precision-sample-5.md` exists (seed 3005, ungraded). Full suite green.
- LLM spend ≤ $3, token-logged.

## Report
Own `reports/shot7-report.md` (Agent A appends only its `### Agent A — regex guards` section). Include: validator design + the frozen rubric verbatim; dev-check confusion matrix + per-edge log path; dedupe evidence; combined re-filter table (counts through each stage); confirmation `3b-precision-sample-5.md` exists; LLM spend; deviations; open questions.

## Boundaries
Do NOT touch: `stockbot/bots/frontier/guards.py` or Agent A's fixtures (Agent A owns the regex layer — you call it, you don't edit it), the dashboard, the race/shadow/fade lanes, `paper_run`, exits, risk, PIT, or any existing test. Your final re-filter waits on Agent A's guards being merged and green.

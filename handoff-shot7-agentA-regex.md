# Shot 7 — Agent A brief: regex guard round 4 (sharper patterns for the 6 round-4 failures)

**You are one of two parallel agents on shot 7. You own ONLY the regex guards.** Pure offline pattern work — no LLM, no network. A cheap-to-mid model is right for this; the failure taxonomy is the spec and the fixtures are the test. Agent B owns the LLM validator and the final re-filter; you won't touch those files.

## Required reading
1. `reports/3b-grading-4.md` — the 6 round-4 failures, with the exact quotes and reasons. This is your spec.
2. `reports/3b-grading-3.md` — the 18 round-3 failures (must STAY dead).
3. `reports/3b-precision-sample-4.md` + `-3.md` — the graded quotes to encode as fixtures.
4. `stockbot/bots/frontier/guards.py` — the rounds 1–3 guard stack you extend (do NOT fork).

## The 6 round-4 failures → what each new/extended guard must catch
The round-3 guards missed these because the surface form was new. Generalize, don't hard-code the specific tickers. Each guard logs its name on every kill (same convention as G1–G7).

1. **AMBQ → Huawei "41%" — stale-year in PROSE (extend G6).** Quote: *"Huawei accounted for ~41% of our net sales during **2024** and **0.1%** during **2025**."* G6 handled multi-year *tables*; it missed the prose form "X% during {year} and Y% during {later year}". Generalize G6: when the quote ties the claimed magnitude to a year label that is **not the latest year present**, and a later year shows a materially different (especially lower) figure, fail. Also covers the relationship having collapsed ("historically was").
2. **AQST → Pharmanovia — direction reversed (extend G7).** Quote: *"Aquestive will serve as the exclusive sole manufacturer and supplier of Libervant."* G7 caught "X is **our** sole supplier" (counterparty = supplier). It missed the inverse: **the filer** is the subject of "will serve as / is the sole manufacturer/supplier of {product}" → the filer is the supplier, so the counterparty is a CUSTOMER, not the filer's supplier. Reclassify/kill these as reversed `sole_supplier`. (Same defect as round-3 #2, same company — make sure the generalized pattern would have killed both.)
3. **AUPH → LUPKYNIS — counterparty is a PRODUCT (new guard, G8).** Quote: *"LUPKYNIS is our only approved product and our only source of net product sales."* Pattern: counterparty is described as the filer's **product**, not a supplier — e.g. "{X} is our only (approved) product", "our only source of (net) product sales", "{X}, our lead/sole product candidate". Kill `sole_supplier`/customer edges whose counterparty is the filer's own product.
4. **EXPO → "consumer products industry" — non-entity industry (extend G1/G2).** Quote: *"21%… of revenues to clients in the consumer products industry."* Counterparty is an industry/vertical/sector/market, not a customer. Kill counterparties ending in / described as "industry", "sector", "vertical", "market", "channel", "end market" — same non-entity class as round-3's "Italy" geography.
5. **GNE → GRE "95.3%" — internal segment by self-reference (extend G1).** Quote: *"GRE's revenue represented ~95.3% of our total consolidated revenue."* The tell is **self-composition**: "{X}'s revenue represented/accounted for Y% of **our** (total/consolidated) revenue" — that's a segment-of-self, not a customer buying from the filer. G1 missed it because "GRE" is an acronym with no lowercase unit-noun suffix. Add a self-reference pattern: counterparty whose revenue is stated as a share **of the filer's own consolidated/total revenue** (as opposed to the filer's sales **to** the counterparty) → internal entity, kill. Be careful NOT to kill legitimate "sales **to** {customer} represented Y% of our revenue" (that's a real customer).
6. **ZURA → FDA — hypothetical boilerplate (new guard, G9, regulatory_precedent only).** Quote: *"These restrictions **may include** … oversight … under a 'consent decree,' which **frequently includes** …"* — generic risk-factor language, no actual action. Require an **actual, attributable action** for `regulatory_precedent`: the filer as a party to a dated action ("we/the Company entered into", "{filer} received", "settled", "consent decree dated…", a dollar penalty, a specific year). Kill `regulatory_precedent` edges whose quote is purely hedged/hypothetical ("may", "could", "frequently includes", "for example", "such as") with no concrete action by the filer.

## Sequencing rule (pre-registration)
Freeze every new/extended pattern in code, with the 50+50 graded edges encoded as **frozen fixtures**, BEFORE anyone runs the full-graph re-filter. The fixtures are a pass/fail gate, not a training set — do not edit them, and do not tune a pattern to chase a single fixture at the cost of the general rule. If a new guard creates collateral (kills a previously-CORRECT edge), report it with the guard name and a one-line justification; a small, declared collateral budget is acceptable, silent recall loss is not.

## Acceptance (your fixtures)
- New test file (e.g. `tests/test_shot7_regex_guards.py`): encode all 50 round-4 + all 50 round-3 graded edges. Assert:
  - all **6 round-4 failures** killed/demoted, each by its **expected guard** (pre-register the attribution map, as shot 6 did with `EXPECTED_GUARDS`);
  - all **18 round-3 failures** still killed;
  - retained-correct floor: do not drop round-3's 31/32 retained, and keep ≥ a stated floor of round-4's 44 correct (report any new collateral by name).
- Existing `tests/test_shot6_3aH_guards.py` stays green (your changes are additive/generalizing, not regressions). Full suite green: `.\.venv\Scripts\python.exe -m pytest tests/ -q`.

## Report
Append a clearly-headed `### Agent A — regex guards` section to `reports/shot7-report.md` (Agent B owns the rest; don't overwrite it): files touched, new/extended guards with the pattern intent, fixture results (6/6 + 18/18 killed by correct guard, retained-correct counts, collateral list), and any pattern where you deliberately chose recall over precision (or vice-versa) so the reviewer can weigh it.

## Boundaries
Do NOT touch: `stockbot/research/refilter_graph.py` or any new validator module (Agent B owns those), the dashboard, the race/shadow/fade lanes, `paper_run`, exits, risk, PIT, or any existing test. No LLM calls, no network. Stop when your fixtures + the suite are green; Agent B runs the combined re-filter.

# Persist per-model probabilities → full-fidelity debate view (small, do before July)

End-to-end build brief, **one ticket, one mid-tier agent**. Small + additive. Reviewer audits. **Time-relevant:** land before July's fade activity so the data accrues with full fidelity (you can't backfill what wasn't stored).

## Why
The AI debate view (shot 13) + the model-disagreement tracker can show *that* Opus and GPT disagreed (the `ensemble_disagreement` flag), but **not who said what** — because the fade decision row doesn't persist the per-model numbers. The `FadeAssessment` already computes `model_probabilities` and `model_classifications` (who said which classification + probability), but `ride.py` doesn't copy them into the decision `inputs.fade_gate`, so they're lost. This makes the debate view say "they disagreed" instead of "Opus 0.70 overreaction vs GPT 0.50 proportionate" — and that detail is what informs the fade-v2 prompt tuning.

## Scope
1. **Persist (the core, ~2 lines):** in `stockbot/bots/proportionality/ride.py`, where the fade entry builds the `gate_inputs` dict for `record_decision` (the dict that already carries `counter_pass`, `flags`, etc.), add:
   - `"model_probabilities": dict(getattr(assessment, "model_probabilities", {}) or {})`
   - `"model_classifications": dict(getattr(assessment, "model_classifications", {}) or {})`
   Defensive `getattr`, same style as the existing fields. Do NOT change any decision logic — purely additive logging.
2. **Surface (debate view):** in the dashboard debate drill-down (the shot-13 "rubric round" section), render the per-model breakdown when present — e.g. `Opus 0.70 (overreaction) · GPT 0.50 (proportionate)` — and visually mark a disagreement. Read-only, from the now-persisted `inputs.fade_gate.model_probabilities`/`model_classifications`. Degrade gracefully on older rows that lack the fields (show the ensemble result as today).

## Hard rules
1. Additive only — no change to the fade gate logic, thresholds, or any decision. The new keys are logging.
2. Don't disturb other lanes/strategies; existing tests stay green.
3. Dashboard part is read-only (no new write paths), stdlib HTML, sentence case, Central time — same as the other dashboard work.

## Acceptance
- A fade decision row's `inputs.fade_gate` now contains `model_probabilities` + `model_classifications` (test against a crafted fade decision through the strategy).
- The debate view renders per-model probabilities + classifications with disagreement marked; older rows without the fields still render (graceful fallback) — both covered by a test.
- Full suite green; dashboard restarted + a debate drill-down verified at :8765.

## Report (`reports/per-model-probs-report.md`)
Files+tests; the two persisted fields; the debate-view rendering (snippet); graceful fallback on old rows; deviations.

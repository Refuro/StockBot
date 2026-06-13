# Shadow lane — extend to mirror the FULL gated lane (ride + fade)

End-to-end build brief, **one ticket, one capable-leaning-mid agent** (touches the live-mode pipeline + the live-broker safety guard — small but safety-sensitive). Reviewer audits. Unblocks the shadow-week review → the $500 pilot.

## Why
The shadow lane (`proportionality_shadow`) was built in shot 5b to rehearse the **ride** pipeline, before the fade strategy existed. It was never extended. But the gated lane now trades **ride + fade**, and the pilot will too — so the shadow is currently an **incomplete rehearsal**: it mirrors ride decisions but ignores fade entirely. The shadow-week review can't certify the live pipeline on a mirror that skips half of what the pilot will do. This makes the shadow lane shadow **both** trade types.

## What the shadow lane is (don't change these invariants)
- Runs on the **live-data feed** in the daily tick, same events as the gated lane.
- Its context is `shadow=True`: every order is logged as a `shadow_order` decision row and **can never reach a live broker** (ctor-level guard). This guard is the whole safety basis — it must still hold for the fade path.
- It inherits the gated lane's surprise history (already working). It starts **flat on positions** and is NOT a position clone of the gated lane — see "parity scope" below.

## Scope
1. **Wire the shadow lane with the SAME `crash_detector` + `fade_gate` INSTANCES the gated lane uses** (in `paper_run.run_paper_tick` / the shadow factory). Sharing the instances means the per-event cache serves the shadow lane its assessment for free — **zero extra LLM spend** — and guarantees the shadow's gate output equals the gated lane's for the same event. Enable fade on the shadow strategy (`fade_enabled=True` in its `RideConfig`, like the gated lane when `fade` is on).
2. Shadow fade theses/decisions book under `proportionality_shadow` with bucket `crash:fade` (and ride under `earnings:ride`), logged as `shadow_order` / the shadow decision actions — never real orders.
3. **Re-verify the live-broker guard for the fade path:** a fade entry in the shadow lane must hit the same ctor-level block — assert in a test that a shadow context can never construct/submit a live order, on the fade path as well as ride.

## Parity scope (what "decisions match" means — for the review + the tests)
- **Gate-level parity (the real criterion):** for each shared event, the shadow lane's brain produces the **same assessment and enter/skip/veto decision** as the gated lane (same quality score, same fade rubric/counter-pass veto). This is deterministic given shared gate instances + shared events.
- **Position-dependent decisions may legitimately differ** and are NOT failures: the max-2-concurrent fade cap, "already holding" skips, and exits all depend on each lane's current book, and the shadow starts flat / diverges from the gated lane's legacy positions. The shadow is a decision-parity + operational rehearsal, **not** a position clone — do not try to clone the gated lane's open positions into it.

## Hard rules
1. The shadow context **can never reach a live broker** — ride or fade. Verify by test.
2. Shared gate instances → **no extra LLM spend** (verify call counts / cache reuse, like the shot-11 shared-ensemble test).
3. Don't disturb the gated / mech / fade-mech / v2 / frontier lanes or their semantics; existing tests stay green.
4. Don't retroactively seed positions; shadow mirrors decisions **forward** from this change.

## Acceptance
- On a scratch tick with a crash (shadow-demo pattern), the shadow lane produces the **same fade gate assessment + decision** as the gated lane (logged as a shadow row), with **zero extra LLM calls** (shared cache).
- Test: shadow fade entry is blocked at the broker guard (never a live order).
- Test: shared-instance reuse (no double gate spend).
- Existing suite green; one manual scratch tick shows the shadow lane logging both ride and fade decisions without disturbing production state.

## Note on the review timeline (for the reviewer, not the builder)
Even after this lands, certifying the shadow week needs **accumulated event-for-event gate matches across both trade types** — which realistically wants July's denser earnings + crash activity. So this ticket makes the shadow a *complete* mirror; the *clock* still needs a real sample before the pilot funds. Expect the pilot to move from late June into July.

## Report (`reports/shadow-ride-plus-fade-report.md`)
Files+tests; the shared-instance wiring; proof of zero extra spend; the broker-guard test for the fade path; the scratch-tick evidence (shadow logging ride + fade decisions matching the gated lane); deviations.

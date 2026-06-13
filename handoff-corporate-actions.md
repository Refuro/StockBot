# Corporate actions (splits) — position + price integrity

**Priority: PRE-PILOT BLOCKER.** One ticket. Mid-tier agent for the mechanical work; reviewer verifies the recomputation. Touches the position/price path, so **no strategy-logic or threshold changes** — this is an integrity fix, not a tuning change.

## The bug (reviewer-found, 2026-07-27)

`MQ` did a **1-for-4 reverse split on 2026-07-01** (Tiingo `splitFactor: 0.25`). `frontier_mech` held it 6/30 → 7/7:

| | value |
|---|---|
| entry (raw close 6/30) | $4.0617 |
| exit (raw close 7/7) | $15.9235 |
| recorded P/L | **+$8,519.54 (+292%)** |
| true P/L (adjClose 16.24 → 15.93) | **−$55.69 (−1.91%)** |
| phantom gain | **$8,575.23** |

Two independent defects combine:

1. **Held quantities are never split-adjusted.** Nothing in `stockbot/` references `splitFactor` / corporate actions. The lane still believed it held 718.23 shares after a 1-for-4 split that left it 179.56.
2. **The frontier resolution price path is unadjusted.** `LivePriceSnapshot` (`bots/frontier/propagation.py:1208`) reads IEX `last`/`prevClose` — **raw**. The ingestion path (`data/real_loader.py:170`) correctly prefers `adjClose`, so *signal detection is already safe*; the mismatch is that entry/exit marks for frontier come from the raw feed while history is adjusted.

## Why this is a pre-pilot blocker

In **live**, the resolution SELL submits the stored quantity — 718 shares against a 180-share position. That is a rejected order at best, and a wrong-exposure event at worst. A forward split (e.g. 2-for-1, factor 2.0) is the mirror risk: price halves, and though ingestion's `adjClose` protects the crash detector from reading it as a −50% "crash", any *held* position would still be marked on a stale share count.

## Scope

1. **Split-adjust open positions.** On each tick, for every open lot, detect a split between the last mark and now (Tiingo `splitFactor` on the daily bar, already in the payload the loader reads). Apply `quantity /= splitFactor`... — confirm direction against the Tiingo convention (`0.25` = 1-for-4 reverse: shares × 0.25, price ÷ 0.25) and **write a test for both a forward and a reverse split**. Adjust the stored entry price by the same factor so the round trip's return math stays continuous.
2. **Make the frontier price path adjusted**, consistent with the ingestion loader — or, if IEX `last` must stay raw for intraday freshness, apply the cumulative split factor between entry date and mark date before computing returns.
3. **Reconcile the sidecar + broker position state** so quantity, entry price, and any high-water mark all move together (a partially-adjusted lot is worse than an unadjusted one).
4. **Backfill/annotate the contaminated record.** Do **not** silently rewrite history: recompute the MQ round trip to −$55.69 and record the correction explicitly (a correction row or an annotation the dashboard reads), so the experimental record shows both the original error and the fix. Reviewer decides final presentation.
5. **Guardrail:** log a loud warning whenever a held symbol's mark moves more than ±50% between consecutive ticks — a real move that size is rare, a split is not.

## Hard rules

1. No changes to pre-registered constants, thresholds, or entry/exit rules. This is price/quantity integrity only.
2. Backtest reproducibility: the shot-8 frontier numbers must stay bit-identical for windows containing no splits. State explicitly which historical results change and why.
3. Tests: forward split, reverse split, split on entry day, split on exit day, no-split control.
4. Full suite green.

## Acceptance

- A test holding a position across a 1-for-4 reverse split reports the true ≈−1.9% return, not +292%.
- A test holding across a 2-for-1 forward split reports the true return and does **not** register a phantom crash/shock.
- The MQ round trip reads −$55.69 (or is explicitly annotated as corrected) and `frontier_up`'s coin standing drops from ~+$8,825 to ~+$250.
- Full suite green; dashboard restarted and the Execution table verified.

## Report (`reports/corporate-actions-report.md`)

Files+tests; the adjustment direction and its proof; which historical numbers changed; how the MQ correction is represented; deviations.

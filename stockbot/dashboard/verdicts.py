"""Deterministic plain-English verdict lines for the dashboard (Ticket 4b-3).

Templates only — fill blanks from existing queries; no LLM, no randomness.
"""

from __future__ import annotations

from typing import Any

MEANINGFUL_SAMPLE_TRADES = 40


def _lane_return_from_curve(points: list[dict[str, Any]]) -> float | None:
    if len(points) < 2:
        return None
    start = points[0]["total_equity"]
    if not start:
        return None
    return points[-1]["total_equity"] / start - 1.0


def race_verdict(
    race: dict[str, Any],
    *,
    gated_bot_id: str,
    mech_bot_id: str,
) -> str:
    """One plain-English sentence comparing AI-gated vs mechanical control."""
    gated = race.get("summary", {}).get("gated") or {}
    mech = race.get("summary", {}).get("mech") or {}
    n_gated = int(gated.get("resolved_trades") or 0)
    n_mech = int(mech.get("resolved_trades") or 0)

    if n_gated == 0 and n_mech == 0:
        return "No trades have resolved yet — nothing to compare. The race just started."

    curves = race.get("curves") or {}
    ret_g = _lane_return_from_curve(curves.get(gated_bot_id) or [])
    ret_m = _lane_return_from_curve(curves.get(mech_bot_id) or [])

    if ret_g is None or ret_m is None:
        return (
            "Not enough equity snapshots yet to compare the lanes — "
            "check back after a few paper ticks."
        )

    diff_pct = (ret_g - ret_m) * 100.0
    if abs(diff_pct) < 0.005:
        relation = "tied with"
    elif diff_pct > 0:
        relation = "ahead of"
    else:
        relation = "behind"

    n = max(n_gated, n_mech)
    sample_msg = (
        "too early to mean anything until ~40 trades"
        if n < MEANINGFUL_SAMPLE_TRADES
        else "a meaningful sample now"
    )
    return (
        f"After {n} resolved trades, the AI lane is {relation} the coin-flip lane "
        f"by {abs(diff_pct):.2f}% — {sample_msg}."
    )


def calibration_verdict_line(row: dict[str, Any]) -> str:
    """One bucket's calibration gate status in plain English."""
    bucket = row.get("strategy_bucket") or "(untagged)"
    k = int(row.get("count") or 0)
    brier = row.get("mean_brier")
    b_str = f"{brier:.4f}" if brier is not None else "—"
    status = "eligible" if row.get("calibrated") else "not yet eligible"
    return (
        f"Bucket {bucket}: {k} of 10 required predictions resolved, "
        f"Brier {b_str} — {status} for real-money consideration."
    )


def calibration_verdicts(rows: list[dict[str, Any]]) -> list[str]:
    """Verdict line(s) for the calibration panel; handles empty DB gracefully."""
    if not rows:
        return [
            "No calibration data yet — no bucket has enough resolved predictions to grade."
        ]
    return [calibration_verdict_line(r) for r in rows]

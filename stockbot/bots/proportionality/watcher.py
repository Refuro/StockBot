"""Earnings watcher — the productionized earnings-event path (Ticket 2a).

One canonical translation from raw feed events to validated, typed earnings
surprises. Strategies and studies consume `EarningsSurprise` records produced
here; nobody parses raw payloads or trusts a precomputed `surprise_pct` field.

Validation rules (mechanical, no LLM):
- only `event_type == "earnings"` events are considered;
- the payload MUST carry both `actual_eps` and `consensus_eps`; the surprise is
  RECOMPUTED from them via `standardized_surprise` — a stored `surprise_pct`
  is treated as advisory and is cross-checked, never trusted;
- events with missing/non-finite/zero-consensus inputs are dropped (and
  counted), never patched with a fake constant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from stockbot.bots.proportionality.surprise import standardized_surprise
from stockbot.types import MarketEvent

# Tolerance when cross-checking a payload's precomputed surprise_pct against
# our own computation (loose: catches sign/magnitude bugs, not float noise).
SURPRISE_MISMATCH_TOLERANCE = 1e-6


@dataclass(frozen=True)
class EarningsSurprise:
    """A validated earnings print with a standardized surprise."""

    event_id: str
    symbol: str
    as_of: datetime
    actual_eps: float
    consensus_eps: float
    surprise_pct: float
    payload: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


@dataclass
class WatcherStats:
    seen: int = 0
    accepted: int = 0
    not_earnings: int = 0
    missing_inputs: int = 0
    undefined_surprise: int = 0
    mismatched_payload_surprise: int = 0


def _finite_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def extract_earnings_surprises(
    events: Iterable[MarketEvent],
    stats: WatcherStats | None = None,
) -> list[EarningsSurprise]:
    """Validate raw feed events into typed earnings surprises (see module doc)."""
    stats = stats if stats is not None else WatcherStats()
    out: list[EarningsSurprise] = []
    for event in events:
        stats.seen += 1
        if event.event_type != "earnings":
            stats.not_earnings += 1
            continue
        actual = _finite_float(event.payload.get("actual_eps"))
        consensus = _finite_float(event.payload.get("consensus_eps"))
        if actual is None or consensus is None:
            stats.missing_inputs += 1
            continue
        surprise = standardized_surprise(actual, consensus)
        if surprise is None:
            stats.undefined_surprise += 1
            continue
        claimed = _finite_float(event.payload.get("surprise_pct"))
        if claimed is not None and abs(claimed - surprise) > SURPRISE_MISMATCH_TOLERANCE:
            # Recomputed value wins; count the discrepancy for the audit trail.
            stats.mismatched_payload_surprise += 1
        stats.accepted += 1
        out.append(
            EarningsSurprise(
                event_id=event.event_id,
                symbol=event.symbol,
                as_of=event.as_of,
                actual_eps=actual,
                consensus_eps=consensus,
                surprise_pct=surprise,
                payload=dict(event.payload),
            )
        )
    return out

"""Standardized earnings surprise — the one canonical computation (Ticket 2a).

Every component that needs a surprise (data loader, watcher, strategy, study)
imports THIS function. Nothing else in the codebase computes a surprise, so a
hand-checked value here is hand-checked everywhere.
"""

from __future__ import annotations

import math


def standardized_surprise(actual_eps: float, consensus_eps: float) -> float | None:
    """(actual - consensus) / |consensus|. None when undefined.

    Undefined cases (return None, never a fake constant):
    - consensus of exactly 0 (division undefined)
    - non-finite inputs
    """
    if not (math.isfinite(actual_eps) and math.isfinite(consensus_eps)):
        return None
    if consensus_eps == 0:
        return None
    return (actual_eps - consensus_eps) / abs(consensus_eps)

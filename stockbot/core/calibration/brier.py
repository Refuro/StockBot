"""Brier score utilities."""

from __future__ import annotations


def brier_score(stated_probability: float, outcome: bool) -> float:
    """Brier score for a single binary prediction. Lower is better; 0 = perfect."""
    actual = 1.0 if outcome else 0.0
    return (stated_probability - actual) ** 2


def mean_brier(scores: list[float]) -> float | None:
    if not scores:
        return None
    return sum(scores) / len(scores)

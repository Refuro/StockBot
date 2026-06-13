"""Calibration ledger — records predictions vs outcomes for gating."""

from __future__ import annotations

from datetime import datetime

from stockbot.core.calibration.brier import brier_score
from stockbot.core.state.store import StateStore


class CalibrationLedger:
    def __init__(self, store: StateStore) -> None:
        self._store = store

    @staticmethod
    def strategy_bucket(event_type: str, direction: str) -> str:
        """Compose the capital-allocation bucket key: "<event_type>:<direction>".

        Examples: "earnings:ride", "crash:fade", "concentration:long", "adr_lag:long".
        Capital is allocated and killed per this key (engine = bot_id is implicit).
        """
        return f"{event_type}:{direction}"

    def record_prediction(
        self,
        thesis_id: str,
        bot_id: str,
        symbol: str,
        direction: str,
        stated_probability: float,
        trade_type: str,
        mode: str,
        rationale: str | None = None,
        strategy_bucket: str = "",
    ) -> None:
        self._store.record_thesis(
            thesis_id=thesis_id,
            bot_id=bot_id,
            symbol=symbol,
            direction=direction,
            stated_probability=stated_probability,
            mode=mode,
            rationale=rationale,
            strategy_bucket=strategy_bucket,
        )

    def resolve(
        self,
        thesis_id: str,
        bot_id: str,
        trade_type: str,
        stated_probability: float,
        outcome: bool,
        bucket: str,
        resolved_at: datetime | None = None,
        strategy_bucket: str = "",
    ) -> float:
        self._store.resolve_thesis(thesis_id, outcome, resolved_at)
        return self._store.record_calibration(
            thesis_id=thesis_id,
            bot_id=bot_id,
            trade_type=trade_type,
            stated_probability=stated_probability,
            outcome=outcome,
            bucket=bucket,
            resolved_at=resolved_at,
            strategy_bucket=strategy_bucket,
        )

    def bucket_for_probability(self, p: float) -> str:
        if p < 0.55:
            return "low_confidence"
        if p < 0.70:
            return "medium_confidence"
        return "high_confidence"

    def is_bucket_calibrated(self, bot_id: str, bucket: str, max_mean_brier: float = 0.25) -> bool:
        stats = self._store.get_calibration_stats(bot_id, bucket)
        if stats["count"] < 10:
            return False
        mean = stats["mean_brier"]
        return mean is not None and mean <= max_mean_brier

    def is_strategy_bucket_calibrated(
        self, bot_id: str, strategy_bucket: str, max_mean_brier: float = 0.25, min_samples: int = 10
    ) -> bool:
        """The capital gate: a strategy bucket trades real money only when this is True."""
        stats = self._store.get_calibration_stats(bot_id, strategy_bucket=strategy_bucket)
        if stats["count"] < min_samples:
            return False
        mean = stats["mean_brier"]
        return mean is not None and mean <= max_mean_brier

    @staticmethod
    def compute_brier(stated_probability: float, outcome: bool) -> float:
        return brier_score(stated_probability, outcome)

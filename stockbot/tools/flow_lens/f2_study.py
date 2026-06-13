"""F2 Flow validation study — consensus-onset vs off-exchange volume proxy."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

from stockbot.config import AppConfig, load_config
from stockbot.core.state.store import StateStore


@dataclass(frozen=True)
class HorizonEffect:
    horizon_days: int
    sample_count: int
    mean_abnormal_ratio: float | None
    median_abnormal_ratio: float | None
    positive_rate: float | None


@dataclass(frozen=True)
class FlowStudyResult:
    start_date: str
    end_date: str
    onset_count: int
    horizons: tuple[HorizonEffect, ...]
    report_text: str


def run_flow_validation_study(
    *,
    finra_csv_dir: str | Path,
    config: AppConfig | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    min_consensus_score: float = 0.5,
    lookback_days: int = 20,
    max_horizon_days: int = 5,
) -> FlowStudyResult:
    """Compute F2 effect sizes from F1 novelty onsets and FINRA volume proxies."""
    cfg = config or load_config()
    store = StateStore(cfg.state.db_path)
    vol = load_finra_off_exchange_volume(finra_csv_dir)
    if not vol:
        return _empty_result("No FINRA off-exchange rows found.", start_date, end_date, max_horizon_days)

    observed_dates = sorted({d for _, d in vol})
    start = start_date or observed_dates[0]
    end = end_date or observed_dates[-1]
    if end < start:
        raise ValueError("end_date must be >= start_date")

    onsets = store.get_flow_lens_consensus_range(
        start.isoformat(),
        end.isoformat(),
        min_score=min_consensus_score,
        novelty_only=True,
    )
    onset_rows = [(row["symbol"], date.fromisoformat(row["logged_date"])) for row in onsets]
    if not onset_rows:
        return _empty_result(
            "No novelty onsets in selected date range.",
            start,
            end,
            max_horizon_days,
        )

    horizon_effects: list[HorizonEffect] = []
    for h in range(1, max_horizon_days + 1):
        ratios: list[float] = []
        for symbol, onset_day in onset_rows:
            baseline = _baseline_avg_volume(vol, symbol, onset_day, lookback_days)
            if baseline is None or baseline <= 0:
                continue
            future = _future_avg_volume(vol, symbol, onset_day, h)
            if future is None:
                continue
            ratios.append((future - baseline) / baseline)
        horizon_effects.append(
            HorizonEffect(
                horizon_days=h,
                sample_count=len(ratios),
                mean_abnormal_ratio=(mean(ratios) if ratios else None),
                median_abnormal_ratio=(median(ratios) if ratios else None),
                positive_rate=((sum(1 for r in ratios if r > 0) / len(ratios)) if ratios else None),
            )
        )

    report = render_flow_study_report(
        start=start,
        end=end,
        onset_count=len(onset_rows),
        effects=horizon_effects,
        lookback_days=lookback_days,
        min_consensus_score=min_consensus_score,
    )
    return FlowStudyResult(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        onset_count=len(onset_rows),
        horizons=tuple(horizon_effects),
        report_text=report,
    )


def load_finra_off_exchange_volume(finra_csv_dir: str | Path) -> dict[tuple[str, date], float]:
    """Load daily off-exchange volume rows from one or more CSV files.

    Expected columns (case-insensitive aliases supported):
    - date/trade_date/session_date (YYYY-MM-DD)
    - symbol/ticker
    - off_exchange_volume/volume/total_volume
    """
    out: dict[tuple[str, date], float] = {}
    for path in sorted(Path(finra_csv_dir).glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol_raw = _row_value(row, ("symbol", "ticker"))
                date_raw = _row_value(row, ("date", "trade_date", "session_date"))
                vol_raw = _row_value(row, ("off_exchange_volume", "volume", "total_volume"))
                if not symbol_raw or not date_raw or not vol_raw:
                    continue
                try:
                    d = date.fromisoformat(str(date_raw))
                    v = float(str(vol_raw))
                except ValueError:
                    continue
                symbol = str(symbol_raw).upper()
                key = (symbol, d)
                out[key] = out.get(key, 0.0) + v
    return out


def render_flow_study_report(
    *,
    start: date,
    end: date,
    onset_count: int,
    effects: list[HorizonEffect],
    lookback_days: int,
    min_consensus_score: float,
) -> str:
    lines = [
        "# F2 Flow Validation Study",
        "",
        f"- Window: {start.isoformat()} to {end.isoformat()}",
        f"- Novelty onset events: {onset_count}",
        f"- Consensus threshold: {min_consensus_score:.2f}",
        f"- Baseline lookback: {lookback_days} prior calendar days with observed volume",
        "",
        "## Effect Sizes (Abnormal Off-Exchange Volume)",
        "",
        "| Horizon (days) | Samples | Mean abnormal | Median abnormal | Positive rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for e in effects:
        lines.append(
            "| "
            f"{e.horizon_days} | {e.sample_count} | {_fmt_ratio(e.mean_abnormal_ratio)} | "
            f"{_fmt_ratio(e.median_abnormal_ratio)} | {_fmt_ratio(e.positive_rate)} |"
        )
    lines.extend(
        [
            "",
            "Interpretation:",
            "- Positive mean/median abnormal ratios suggest novelty onsets coincide with elevated retail-flow proxy.",
            "- If effects remain near zero after 4–8 weeks, keep Flow Lens as a logger only (no overlay).",
        ]
    )
    return "\n".join(lines)


def _baseline_avg_volume(
    vol: dict[tuple[str, date], float],
    symbol: str,
    onset_day: date,
    lookback_days: int,
) -> float | None:
    vals: list[float] = []
    for i in range(1, lookback_days + 1):
        d = onset_day - timedelta(days=i)
        v = vol.get((symbol, d))
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return mean(vals)


def _future_avg_volume(
    vol: dict[tuple[str, date], float],
    symbol: str,
    onset_day: date,
    horizon_days: int,
) -> float | None:
    vals: list[float] = []
    for i in range(1, horizon_days + 1):
        d = onset_day + timedelta(days=i)
        v = vol.get((symbol, d))
        if v is None:
            return None
        vals.append(v)
    return mean(vals) if vals else None


def _row_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for a in aliases:
        if a in lowered:
            return lowered[a]
    return None


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


def _empty_result(
    reason: str,
    start_date: date | None,
    end_date: date | None,
    max_horizon_days: int,
) -> FlowStudyResult:
    start = start_date.isoformat() if start_date else "n/a"
    end = end_date.isoformat() if end_date else "n/a"
    horizons = tuple(
        HorizonEffect(
            horizon_days=h,
            sample_count=0,
            mean_abnormal_ratio=None,
            median_abnormal_ratio=None,
            positive_rate=None,
        )
        for h in range(1, max_horizon_days + 1)
    )
    report = "\n".join(
        [
            "# F2 Flow Validation Study",
            "",
            f"- Window: {start} to {end}",
            f"- Result: {reason}",
        ]
    )
    return FlowStudyResult(
        start_date=start,
        end_date=end,
        onset_count=0,
        horizons=horizons,
        report_text=report,
    )


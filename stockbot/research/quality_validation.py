"""Ticket 2c validation study — labeled sanity check on POST-CUTOFF events.

PRE-REGISTERED DESIGN (hard rule 4 — written before any event was scored;
nothing below may be tuned after seeing results):

- Eligible events: earnings events in the dataset with event timestamp on or
  after ``CUTOFF_DATE`` (2026-02-01, past all runtime models' training
  cutoffs), whose standardized surprise passes the SAME pre-registered
  mechanical rule as 2b: top decile of all prior surprises (expanding window,
  min 40 prior observations).
- Label (mechanical): entry price = first price bar STRICTLY AFTER the event
  timestamp; exit price = first bar at or after entry + ``LABEL_HOLD_DAYS``
  (10) calendar days. label = "drifter" if net return > 0 else "fader".
  Events lacking either price are excluded (counted).
- Score: the full 2c pipeline (Haiku triage -> Opus+GPT ensemble, with the
  pre-registered disagreement and degradation rules) on the 8-K text.
- Spend cap: $25 total, enforced by the router. If the cap trips mid-study,
  the partial sample is reported as-is.
- Acceptance readout: mean quality score of drifters vs faders, stated
  plainly with sample size. This is a sanity check, not proof — forward
  paper is the proof.

Run:
    python -m stockbot.research.quality_validation --data-dir data/real_expanded
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from stockbot.bots.proportionality.quality import (
    PROMPT_VERSION,
    QUALITY_PROBABILITY_ENTRY_THRESHOLD,
    fetch_event_filing_text,
    score_earnings_event,
)
from stockbot.bots.proportionality.ride import percentile_nearest_rank
from stockbot.bots.proportionality.watcher import extract_earnings_surprises
from stockbot.config import load_env
from stockbot.core.llm import LLMSpendCapExceeded, build_router
from stockbot.data.real_loader import CachedHTTP
from stockbot.types import MarketEvent

logger = logging.getLogger(__name__)

# --- pre-registered constants (do not touch after the study runs) -------------
CUTOFF_DATE = datetime(2026, 2, 1, tzinfo=timezone.utc)
LABEL_HOLD_DAYS = 10
DECILE_PCT = 0.90
MIN_HISTORY = 40
SPEND_CAP_USD = 25.0


def _load_events(events_csv: Path) -> list[MarketEvent]:
    out: list[MarketEvent] = []
    with events_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(
                MarketEvent(
                    event_id=row["event_id"],
                    event_type=row["event_type"],
                    symbol=row["symbol"],
                    as_of=datetime.fromisoformat(row["timestamp"]),
                    payload=json.loads(row["payload_json"]),
                )
            )
    out.sort(key=lambda e: e.as_of)
    return out


def _load_prices(prices_csv: Path) -> dict[str, list[tuple[datetime, float]]]:
    out: dict[str, list[tuple[datetime, float]]] = {}
    with prices_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["symbol"], []).append(
                (datetime.fromisoformat(row["timestamp"]), float(row["price"]))
            )
    for series in out.values():
        series.sort(key=lambda x: x[0])
    return out


def label_event(
    prices: list[tuple[datetime, float]], event_ts: datetime
) -> tuple[float, float, float] | None:
    """(entry, exit, net_return) per the pre-registered label, or None."""
    entry = next(((ts, p) for ts, p in prices if ts > event_ts), None)
    if entry is None:
        return None
    exit_cutoff = entry[0] + timedelta(days=LABEL_HOLD_DAYS)
    exit_ = next(((ts, p) for ts, p in prices if ts >= exit_cutoff), None)
    if exit_ is None:
        return None
    net = (exit_[1] - entry[1]) / entry[1]
    return entry[1], exit_[1], net


def select_qualifying_events(events: list[MarketEvent]) -> list[Any]:
    """Post-cutoff events passing the pre-registered expanding top-decile rule."""
    surprises = extract_earnings_surprises(events)
    history: list[float] = []
    qualifying = []
    for s in surprises:
        if s.surprise_pct > 0 and len(history) >= MIN_HISTORY:
            cutoff = percentile_nearest_rank(history, DECILE_PCT)
            if s.surprise_pct >= cutoff and s.as_of >= CUTOFF_DATE:
                qualifying.append(s)
        history.append(s.surprise_pct)
    return qualifying


def run_validation_study(
    *,
    data_dir: Path,
    cache_dir: Path,
    out_path: Path,
    spend_cap_usd: float = SPEND_CAP_USD,
    router=None,
) -> dict[str, Any]:
    env = load_env()
    events = _load_events(data_dir / "events.csv")
    prices = _load_prices(data_dir / "prices.csv")
    qualifying = select_qualifying_events(events)
    logger.info("qualifying post-cutoff events: %d", len(qualifying))

    if router is None:
        router = build_router(cap_usd=spend_cap_usd, label="2c-validation", env=env)
    client = CachedHTTP(cache_dir=cache_dir, offline=False)

    rows: list[dict[str, Any]] = []
    unlabeled = 0
    unscored = 0
    cap_tripped = False
    for s in qualifying:
        series = prices.get(s.symbol, [])
        labeled = label_event(series, s.as_of)
        if labeled is None:
            unlabeled += 1
            continue
        entry_px, exit_px, net = labeled
        payload = dict(s.payload)
        payload.setdefault("surprise_pct", s.surprise_pct)
        cik = payload.get("cik")
        accession = payload.get("accession_number")
        if not cik or not accession:
            unscored += 1
            continue
        try:
            text = fetch_event_filing_text(
                client, cik=int(cik), accession_number=str(accession),
                sec_user_agent=env.sec_user_agent,
            )
            assessment = score_earnings_event(
                router, event_id=s.event_id, symbol=s.symbol,
                payload=payload, filing_text=text,
            )
        except LLMSpendCapExceeded:
            cap_tripped = True
            logger.warning("spend cap reached after %d scored events", len(rows))
            break
        except Exception as exc:  # noqa: BLE001 - per-event isolation
            logger.warning("scoring failed for %s: %s", s.event_id, exc)
            unscored += 1
            continue
        if not assessment.scored:
            unscored += 1
            continue
        rows.append(
            {
                "event_id": s.event_id,
                "symbol": s.symbol,
                "event_ts": s.as_of.isoformat(),
                "surprise_pct": s.surprise_pct,
                "net_return_10d": net,
                "label": "drifter" if net > 0 else "fader",
                "quality_score": assessment.quality_score,
                "stated_probability": assessment.stated_probability,
                "flags": assessment.flags,
                "passes_entry_threshold": assessment.passes_entry_threshold,
            }
        )

    drifters = [r for r in rows if r["label"] == "drifter"]
    faders = [r for r in rows if r["label"] == "fader"]
    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pre_registered": {
            "cutoff_date": CUTOFF_DATE.isoformat(),
            "label": f"sign of {LABEL_HOLD_DAYS}-day net return after event (first bar after event)",
            "entry_rule": f"top decile (expanding, min {MIN_HISTORY} prior)",
            "entry_threshold": QUALITY_PROBABILITY_ENTRY_THRESHOLD,
            "spend_cap_usd": spend_cap_usd,
            "prompt_version": PROMPT_VERSION,
        },
        "n_qualifying": len(qualifying),
        "n_scored": len(rows),
        "n_unlabeled": unlabeled,
        "n_unscored": unscored,
        "cap_tripped": cap_tripped,
        "n_drifters": len(drifters),
        "n_faders": len(faders),
        "mean_quality_drifters": (
            statistics.mean(r["quality_score"] for r in drifters) if drifters else None
        ),
        "mean_quality_faders": (
            statistics.mean(r["quality_score"] for r in faders) if faders else None
        ),
        "mean_prob_drifters": (
            statistics.mean(r["stated_probability"] for r in drifters) if drifters else None
        ),
        "mean_prob_faders": (
            statistics.mean(r["stated_probability"] for r in faders) if faders else None
        ),
        "llm_spend": router.tracker.summary(),
        "rows": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("validation study written: %s", out_path)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="2c validation study (pre-registered)")
    parser.add_argument("--data-dir", default="data/real_expanded")
    parser.add_argument("--cache-dir", default="fixtures/real_cache")
    parser.add_argument("--out", default="reports/2c-validation.json")
    parser.add_argument("--cap", type=float, default=SPEND_CAP_USD)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_validation_study(
        data_dir=Path(args.data_dir),
        cache_dir=Path(args.cache_dir),
        out_path=Path(args.out),
        spend_cap_usd=args.cap,
    )
    d, f = summary["mean_quality_drifters"], summary["mean_quality_faders"]
    print(f"Scored n={summary['n_scored']} (drifters {summary['n_drifters']}, faders {summary['n_faders']})")
    print(f"Mean quality: drifters={d if d is None else round(d, 4)} faders={f if f is None else round(f, 4)}")
    print(f"LLM spend: ${summary['llm_spend']['spent_usd']:.2f} of ${args.cap:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

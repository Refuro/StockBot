"""Ticket 2.5b validation study — labeled sanity check on POST-CUTOFF crashes.

PRE-REGISTERED DESIGN (hard rule 5 — written before any crash was scored;
nothing below may be tuned after seeing results):

- Eligible events: band-filtered crashes (>=10% close-to-close; $300M-$10B
  screen-time cap; >$1M median dollar ADV) detected in the real expanded
  dataset with crash timestamp on or after ``CUTOFF_DATE`` (2026-02-01, past
  all runtime models' training cutoffs).
- Label (mechanical): entry price = first price bar STRICTLY AFTER the crash
  bar (how a fade would actually enter, next-bar); exit price = first bar at
  or after entry + ``LABEL_HOLD_DAYS`` (10) calendar days. label = "reverter"
  if net return > 0 else "continuer". Events lacking either price are
  excluded (counted).
- Score: the full 2.5b pipeline (code hard-skip screen -> Haiku triage ->
  Opus+GPT rubric ensemble -> mandatory adversarial counter-pass with the
  pre-registered veto rule). Entry threshold and all pipeline constants live
  in ``bots/proportionality/fade.py`` and were fixed before this study ran.
- Spend cap: $25 total, enforced by the router. If the cap trips mid-study,
  the partial sample is reported as-is (events are scored chronologically).
- Acceptance readout: mean stated probability of reverters vs continuers,
  plus veto/hard-skip/classification counts — stated plainly with sample
  size. Weak is reportable; silence is not. Forward paper is the proof.

Run:
    python -m stockbot.research.fade_validation --data-dir data/real_expanded
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

from stockbot.bots.proportionality.crash import (
    attach_source_docs,
    detect_crashes_from_prices_csv,
    load_band_symbols,
)
from stockbot.bots.proportionality.fade import (
    FADE_PROBABILITY_ENTRY_THRESHOLD,
    PROMPT_VERSION,
    assess_crash_event,
)
from stockbot.bots.proportionality.quality import fetch_event_filing_text
from stockbot.config import load_env
from stockbot.core.llm import LLMSpendCapExceeded, build_router
from stockbot.data.real_loader import CachedHTTP, load_sec_ticker_map

logger = logging.getLogger(__name__)

# --- pre-registered constants (do not touch after the study runs) -------------
CUTOFF_DATE = datetime(2026, 2, 1, tzinfo=timezone.utc)
LABEL_HOLD_DAYS = 10
SPEND_CAP_USD = 25.0


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


def label_crash(
    prices: list[tuple[datetime, float]], crash_ts: datetime
) -> tuple[float, float, float] | None:
    """(entry, exit, net_return) per the pre-registered label, or None."""
    entry = next(((ts, p) for ts, p in prices if ts > crash_ts), None)
    if entry is None:
        return None
    exit_cutoff = entry[0] + timedelta(days=LABEL_HOLD_DAYS)
    exit_ = next(((ts, p) for ts, p in prices if ts >= exit_cutoff), None)
    if exit_ is None:
        return None
    net = (exit_[1] - entry[1]) / entry[1]
    return entry[1], exit_[1], net


def run_fade_validation(
    *,
    data_dir: Path,
    universe_metadata: Path,
    cache_dir: Path,
    out_path: Path,
    spend_cap_usd: float = SPEND_CAP_USD,
    router=None,
) -> dict[str, Any]:
    env = load_env()
    band = load_band_symbols(universe_metadata)
    crashes = [
        c
        for c in detect_crashes_from_prices_csv(data_dir / "prices.csv", band)
        if c.as_of >= CUTOFF_DATE
    ]
    prices = _load_prices(data_dir / "prices.csv")
    logger.info("post-cutoff band crashes: %d", len(crashes))

    if router is None:
        router = build_router(cap_usd=spend_cap_usd, label="2.5b-validation", env=env)
    client = CachedHTTP(cache_dir=cache_dir, offline=False)
    ticker_map = load_sec_ticker_map(client, env.sec_user_agent)
    crashes = attach_source_docs(crashes, client, ticker_map, env.sec_user_agent)

    rows: list[dict[str, Any]] = []
    unlabeled = 0
    no_doc = 0
    unscored = 0
    hard_skips = 0
    cap_tripped = False
    for ev in crashes:  # chronological by construction
        labeled = label_crash(prices.get(ev.symbol, []), ev.as_of)
        if labeled is None:
            unlabeled += 1
            continue
        entry_px, exit_px, net = labeled
        if ev.source_doc is None:
            no_doc += 1
            continue
        try:
            text = fetch_event_filing_text(
                client, cik=ev.source_doc.cik,
                accession_number=ev.source_doc.accession_number,
                sec_user_agent=env.sec_user_agent,
            )
            assessment = assess_crash_event(router, event=ev, filing_text=text)
        except LLMSpendCapExceeded:
            cap_tripped = True
            logger.warning("spend cap reached after %d scored crashes", len(rows))
            break
        except Exception as exc:  # noqa: BLE001 - per-event isolation
            logger.warning("scoring failed for %s: %s", ev.event_id, exc)
            unscored += 1
            continue
        if assessment.hard_skip:
            hard_skips += 1
        if not assessment.scored and not assessment.hard_skip:
            unscored += 1
            continue
        rows.append(
            {
                "event_id": ev.event_id,
                "symbol": ev.symbol,
                "crash_ts": ev.as_of.isoformat(),
                "drop_pct": ev.drop_pct,
                "source_doc_form": ev.source_doc.form,
                "source_doc_accession": ev.source_doc.accession_number,
                "net_return_10d": net,
                "label": "reverter" if net > 0 else "continuer",
                "scored": assessment.scored,
                "hard_skip": assessment.hard_skip,
                "hard_skip_reasons": assessment.hard_skip_reasons,
                "classification": assessment.classification,
                "ensemble_probability": assessment.ensemble_probability,
                "stated_probability": assessment.stated_probability,
                "vetoed": assessment.vetoed,
                "counter_pass": assessment.counter_pass,
                "forced_flow": assessment.forced_flow,
                "flags": assessment.flags,
                "passes_entry_threshold": assessment.passes_entry_threshold,
            }
        )

    scored = [r for r in rows if r["scored"]]
    reverters = [r for r in scored if r["label"] == "reverter"]
    continuers = [r for r in scored if r["label"] == "continuer"]

    def _mean(rs: list[dict[str, Any]], key: str) -> float | None:
        vals = [r[key] for r in rs if r[key] is not None]
        return statistics.mean(vals) if vals else None

    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pre_registered": {
            "cutoff_date": CUTOFF_DATE.isoformat(),
            "label": (
                f"sign of {LABEL_HOLD_DAYS}-day net return after the crash "
                "(entry = first bar strictly after the crash bar)"
            ),
            "crash_rule": ">=10% close-to-close, $300M-$10B cap band, >$1M median $ADV",
            "entry_threshold": FADE_PROBABILITY_ENTRY_THRESHOLD,
            "spend_cap_usd": spend_cap_usd,
            "prompt_version": PROMPT_VERSION,
        },
        "n_crashes_post_cutoff": len(crashes),
        "n_rows": len(rows),
        "n_scored": len(scored),
        "n_unlabeled": unlabeled,
        "n_no_source_doc": no_doc,
        "n_unscored": unscored,
        "n_hard_skips": hard_skips,
        "cap_tripped": cap_tripped,
        "n_reverters": len(reverters),
        "n_continuers": len(continuers),
        "mean_prob_reverters": _mean(reverters, "stated_probability"),
        "mean_prob_continuers": _mean(continuers, "stated_probability"),
        "mean_ensemble_prob_reverters": _mean(reverters, "ensemble_probability"),
        "mean_ensemble_prob_continuers": _mean(continuers, "ensemble_probability"),
        "n_fade_candidates": sum(1 for r in scored if r["classification"] == "overreaction"),
        "n_vetoes": sum(1 for r in rows if r["vetoed"]),
        "n_entries_would_fire": sum(1 for r in rows if r["passes_entry_threshold"]),
        "llm_spend": router.tracker.summary(),
        "rows": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("fade validation study written: %s", out_path)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="2.5b fade validation study (pre-registered)")
    parser.add_argument("--data-dir", default="data/real_expanded")
    parser.add_argument("--universe-metadata", default="stockbot/data/universe_expanded.metadata.json")
    parser.add_argument("--cache-dir", default="fixtures/real_cache")
    parser.add_argument("--out", default="reports/2.5b-fade-validation.json")
    parser.add_argument("--cap", type=float, default=SPEND_CAP_USD)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_fade_validation(
        data_dir=Path(args.data_dir),
        universe_metadata=Path(args.universe_metadata),
        cache_dir=Path(args.cache_dir),
        out_path=Path(args.out),
        spend_cap_usd=args.cap,
    )
    r, c = summary["mean_prob_reverters"], summary["mean_prob_continuers"]
    print(
        f"Scored n={summary['n_scored']} (reverters {summary['n_reverters']}, "
        f"continuers {summary['n_continuers']}; hard skips {summary['n_hard_skips']}, "
        f"vetoes {summary['n_vetoes']})"
    )
    print(
        f"Mean stated probability: reverters={r if r is None else round(r, 4)} "
        f"continuers={c if c is None else round(c, 4)}"
    )
    print(f"LLM spend: ${summary['llm_spend']['spent_usd']:.2f} of ${args.cap:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

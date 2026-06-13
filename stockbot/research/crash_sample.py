"""Ticket 2.5a acceptance evidence — labeled crash sample + source-doc retrieval.

Runs the mechanical crash detector over the real expanded dataset
(2022-2026), then takes a DETERMINISTIC labeled sample — the 3 largest
close-to-close drops per calendar year (>=15 events across 2022-2026) — and
attaches each crash's primary source document via the cached EDGAR plumbing.

The report lists every sampled crash with its retrieved document (form,
accession, date, URL) plus a verification excerpt, so doc-retrieval
correctness (>=90% bar) can be hand-verified in minutes. The builder records
its own verdict per row; the reviewer can re-check any row from the URL.

Run: python -m stockbot.research.crash_sample --data-dir data/real_expanded
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from stockbot.bots.proportionality.crash import (
    CrashEvent,
    attach_source_docs,
    detect_crashes_from_prices_csv,
    load_band_symbols,
)
from stockbot.bots.proportionality.quality import fetch_event_filing_text
from stockbot.config import load_env
from stockbot.data.real_loader import CachedHTTP, load_sec_ticker_map

logger = logging.getLogger(__name__)

SAMPLE_PER_YEAR = 3  # deterministic: largest |drop| per calendar year


def select_labeled_sample(crashes: list[CrashEvent]) -> list[CrashEvent]:
    by_year: dict[int, list[CrashEvent]] = {}
    for ev in crashes:
        by_year.setdefault(ev.as_of.year, []).append(ev)
    out: list[CrashEvent] = []
    for year in sorted(by_year):
        ranked = sorted(by_year[year], key=lambda e: (e.drop_pct, e.symbol))
        out.extend(ranked[:SAMPLE_PER_YEAR])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="2.5a crash detector labeled sample")
    parser.add_argument("--data-dir", default="data/real_expanded")
    parser.add_argument("--universe-metadata", default="stockbot/data/universe_expanded.metadata.json")
    parser.add_argument("--cache-dir", default="fixtures/real_cache")
    parser.add_argument("--out", default="reports/2.5a-crash-sample.md")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = load_env()
    band = load_band_symbols(args.universe_metadata)
    crashes = detect_crashes_from_prices_csv(Path(args.data_dir) / "prices.csv", band)
    print(f"Band symbols: {len(band)}; crashes detected (>=10% close-to-close): {len(crashes)}")

    sample = select_labeled_sample(crashes)
    client = CachedHTTP(cache_dir=args.cache_dir, offline=False)
    ticker_map = load_sec_ticker_map(client, env.sec_user_agent)
    sample = attach_source_docs(sample, client, ticker_map, env.sec_user_agent)

    by_year: dict[int, int] = {}
    for ev in crashes:
        by_year[ev.as_of.year] = by_year.get(ev.as_of.year, 0) + 1

    lines = [
        "# 2.5a Crash Detector — Labeled Sample + Source-Document Retrieval",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Dataset: `{args.data_dir}` (real Tiingo closes, 2022-2026); band filter:",
        "$300M-$10B screen-time market cap, >$1M median dollar ADV "
        f"({len(band)} of the 1,376-name universe pass).",
        "",
        "**Band-coverage caveat (honest):** the universe itself was screened at",
        "$300M-$3B, so the $3B-$10B slice of the 2.5a band has no coverage;",
        "cap/ADV are screen-time measurements, not point-in-time.",
        "",
        f"Crashes detected, total: **{len(crashes)}**; by year: "
        + ", ".join(f"{y}: {n}" for y, n in sorted(by_year.items())),
        "",
        f"Labeled sample: {SAMPLE_PER_YEAR} largest drops per calendar year "
        f"(deterministic), n={len(sample)}.",
        "",
        "Each entry shows the retrieved primary source document and a document",
        "excerpt. Verdict key: CORRECT = the document substantively explains the",
        "crash; WRONG = a document was retrieved but it does not explain the",
        "crash; NONE = no EDGAR filing in the window (counted against the",
        "retrieval rate only if a causal filing demonstrably exists).",
        "",
        "---",
        "",
    ]
    docs_retrieved = 0
    for i, ev in enumerate(sample, 1):
        lines += [
            f"## {i}. `{ev.symbol}` {ev.drop_pct:+.1%} on {ev.as_of.date().isoformat()}",
            "",
            f"- **Close:** {ev.prev_close:.2f} -> {ev.close:.2f}",
            f"- **Market cap (screen-time):** ${(ev.market_cap or 0)/1e6:,.0f}M; "
            f"median $ADV ${(ev.median_dollar_volume or 0)/1e6:,.2f}M",
        ]
        if ev.source_doc is not None:
            docs_retrieved += 1
            d = ev.source_doc
            lines += [
                f"- **Source doc:** {d.form} `{d.accession_number}`, filed {d.filed_at}",
                f"- **URL:** {d.url}",
            ]
            try:
                text = fetch_event_filing_text(
                    client, cik=d.cik, accession_number=d.accession_number,
                    sec_user_agent=env.sec_user_agent,
                )
                excerpt = " ".join(text.split())[:600]
                lines += ["", f"> {excerpt}…"]
            except Exception as exc:  # noqa: BLE001 - excerpt is evidence, not load-bearing
                lines += ["", f"> (excerpt fetch failed: {exc})"]
        else:
            lines += ["- **Source doc:** NONE found in window [-3, +1] days"]
        lines += ["", "Builder verdict: [ ] CORRECT  [ ] WRONG  [ ] NONE", "", "---", ""]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}: sample n={len(sample)}, docs retrieved {docs_retrieved}/{len(sample)}")

    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "band_symbols": len(band),
        "crashes_total": len(crashes),
        "crashes_by_year": {str(y): n for y, n in sorted(by_year.items())},
        "sample_n": len(sample),
        "docs_retrieved": docs_retrieved,
    }
    stats_path = out.with_suffix(".json")
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""3b precision sample generator: random named edges for reviewer grading.

Draws a seeded random sample of 50 named edges (or all, if fewer) from the
frontier graph and writes `reports/3b-precision-sample.md`, each entry
formatted so a reviewer can verify it against the filing in under a minute.
The builder does NOT grade the sample — the 3b checkpoint belongs to the
reviewer (pre-registered bar: >=90% correct).

Run: python -m stockbot.research.precision_sample
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timezone
from pathlib import Path

from stockbot.core.state.store import StateStore

DEFAULT_SEED = 3001
DEFAULT_SAMPLE_SIZE = 50


def render_precision_sample(
    edges: list[dict],
    *,
    seed: int = DEFAULT_SEED,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> str:
    rng = random.Random(seed)
    pool = sorted(edges, key=lambda e: e["edge_id"])  # deterministic pool order
    sample = pool if len(pool) <= sample_size else rng.sample(pool, sample_size)
    sample.sort(key=lambda e: (e["from_symbol"], e["edge_type"], e["edge_id"]))

    lines = [
        "# 3b Precision Sample — Frontier Graph Named Edges",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Sampling: seeded random (`random.Random({seed})`), sample size "
        f"{len(sample)} of {len(pool)} named edges.",
        "",
        "Grading instructions (reviewer): an edge is CORRECT iff the quote",
        "genuinely asserts the typed relationship at the claimed magnitude AND",
        "the ticker resolution (when present) is right. Pre-registered bar: >=90%.",
        "",
        "---",
        "",
    ]
    for i, e in enumerate(sample, 1):
        to_part = e["counterparty_name"]
        if e["counterparty_symbol"]:
            to_part += f" (resolved: `{e['counterparty_symbol']}`)"
        magnitude = (
            f"{e['magnitude_pct']:g}% of revenue" if e["magnitude_pct"] is not None else "n/a"
        )
        lines += [
            f"## {i}. `{e['from_symbol']}` --[{e['edge_type']}]--> {to_part}",
            "",
            f"- **Magnitude:** {magnitude}",
            f"- **Filing:** {e['form']} `{e['accession_number']}`, filed {e['filed_at']}",
            f"- **URL:** {e['filing_url']}",
            "",
            "> " + e["quoted_passage"].replace("\n", " "),
            "",
            "Verdict: [ ] correct  [ ] incorrect",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="3b precision sample report")
    parser.add_argument("--db", default="data/state.db")
    parser.add_argument("--out", default="reports/3b-precision-sample.md")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    args = parser.parse_args(argv)

    store = StateStore(args.db)
    edges = store.get_frontier_edges(named_only=True)
    report = render_precision_sample(edges, seed=args.seed, sample_size=args.sample_size)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"Wrote {out} ({min(len(edges), args.sample_size)} of {len(edges)} named edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shot-1 candidate universe loader (Phase 2b).

The candidate list (`universe_shot1.csv`) was curated by hand targeting US
small/mid caps in the $300M-$3B market-cap band that have traded since before
mid-2022. Two honesty caveats, enforced/documented downstream:

- Market-cap band membership is approximate (judged at curation time; no
  point-in-time cap screen is wired). The mechanical filter that IS enforced
  from data is ADV: symbols below the dollar-ADV floor are excluded by the
  drift study and listed in its report.
- The list is survivorship-biased: names that were delisted/acquired before
  curation never made the list. The drift report carries this caveat.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_UNIVERSE_PATH = Path(__file__).parent / "universe_shot1.csv"


@dataclass(frozen=True)
class UniverseEntry:
    symbol: str
    sector: str


def load_universe(path: str | Path = DEFAULT_UNIVERSE_PATH) -> list[UniverseEntry]:
    entries: list[UniverseEntry] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = str(row["symbol"]).strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            entries.append(UniverseEntry(symbol=symbol, sector=str(row.get("sector", "")).strip()))
    return entries

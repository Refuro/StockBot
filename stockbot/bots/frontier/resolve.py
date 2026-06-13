"""Counterparty name -> ticker resolution (Phase 3a) — conservative only.

Uses the SEC `company_tickers.json` map with exact-ish matching on a
normalized company name. An unresolved name stays a name; we never
fuzzy-guess tickers (a wrong ticker is worse than no ticker for the graph).
"""

from __future__ import annotations

import re
from typing import Any

from stockbot.data.real_loader import SEC_TICKERS_URL, CachedHTTP

# Legal-suffix tokens stripped during normalization (must not change identity).
_SUFFIX_TOKENS = {
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "LTD",
    "LIMITED",
    "PLC",
    "LLC",
    "LP",
    "NV",
    "SA",
    "AG",
    "SE",
    "HOLDING",
    "HOLDINGS",
    "GROUP",
    "THE",
}

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")


def normalize_company_name(name: str) -> str:
    """Uppercase, strip punctuation and legal suffixes: 'Apple Inc.' -> 'APPLE'."""
    text = _NON_ALNUM_RE.sub(" ", name.upper())
    tokens = [t for t in text.split() if t]
    while tokens and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    while tokens and tokens[0] == "THE":
        tokens.pop(0)
    return " ".join(tokens)


def build_name_resolver(raw_company_tickers: dict[str, Any]) -> dict[str, str]:
    """Normalized company name -> ticker.

    Identical normalized names from the SAME company (share classes, e.g.
    GOOGL/GOOG) resolve to the first-listed ticker. Identical names from
    DIFFERENT companies are ambiguous and removed entirely.
    """
    by_name: dict[str, tuple[str, int]] = {}
    ambiguous: set[str] = set()
    for _, row in sorted(raw_company_tickers.items(), key=lambda kv: int(kv[0])):
        name = normalize_company_name(str(row.get("title", "")))
        if not name:
            continue
        ticker = str(row["ticker"]).upper()
        cik = int(row["cik_str"])
        if name in by_name:
            if by_name[name][1] != cik:
                ambiguous.add(name)
            continue  # same company (share class): keep first-listed ticker
        by_name[name] = (ticker, cik)
    return {name: tv[0] for name, tv in by_name.items() if name not in ambiguous}


def load_name_resolver(client: CachedHTTP, sec_user_agent: str) -> dict[str, str]:
    raw = client.get_json(SEC_TICKERS_URL, headers={"User-Agent": sec_user_agent})
    return build_name_resolver(raw)


def resolve_counterparty(name: str, resolver: dict[str, str]) -> str | None:
    """Exact normalized-name match only; None when unknown or ambiguous."""
    return resolver.get(normalize_company_name(name))

"""Extract US equity tickers from free-form model responses."""

from __future__ import annotations

import re

# Common English / finance words that match ticker-shaped tokens.
_TICKER_BLOCKLIST = frozenset(
    {
        "A",
        "I",
        "AI",
        "AM",
        "AN",
        "AND",
        "ARE",
        "AS",
        "AT",
        "BE",
        "BIG",
        "BY",
        "CEO",
        "CFO",
        "CO",
        "DD",
        "DO",
        "EPS",
        "ETF",
        "EU",
        "EV",
        "FDA",
        "FOR",
        "GDP",
        "GO",
        "HAS",
        "HE",
        "HER",
        "HIM",
        "HIS",
        "IF",
        "IN",
        "IPO",
        "IS",
        "IT",
        "ITS",
        "LLC",
        "LTD",
        "ME",
        "MY",
        "NDA",
        "NEWS",
        "NO",
        "NOT",
        "OF",
        "ON",
        "OR",
        "OTC",
        "OUR",
        "OUT",
        "PE",
        "PM",
        "PT",
        "RE",
        "SEC",
        "SO",
        "TA",
        "THE",
        "TO",
        "TOP",
        "UK",
        "UP",
        "US",
        "USA",
        "VS",
        "WE",
        "YOLO",
        "YOU",
    }
)

_CASH_TAG = re.compile(r"\$([A-Z]{1,5})\b")
_BARE_TAG = re.compile(r"\b([A-Z]{1,5})\b")


def extract_tickers(text: str) -> list[str]:
    """Return deduplicated tickers mentioned in model output (order preserved)."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in _CASH_TAG.finditer(text):
        sym = match.group(1)
        if _is_valid_ticker(sym) and sym not in seen:
            seen.add(sym)
            found.append(sym)
    for match in _BARE_TAG.finditer(text):
        sym = match.group(1)
        if _is_valid_ticker(sym) and sym not in seen:
            seen.add(sym)
            found.append(sym)
    return found


def _is_valid_ticker(symbol: str) -> bool:
    if len(symbol) < 1 or len(symbol) > 5:
        return False
    if not symbol.isalpha():
        return False
    if symbol in _TICKER_BLOCKLIST:
        return False
    return True

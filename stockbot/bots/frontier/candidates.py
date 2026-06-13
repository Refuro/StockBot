"""Regex-first candidate passage finder for typed edges (Phase 3a).

Mechanically locates passages that *might* assert a high-magnitude typed edge.
The LLM (confirm.py) only confirms/types/extracts from these quoted passages —
it never invents an edge the regex pass didn't surface. Materiality is
certified by the disclosure itself (a stated >=20% revenue share, an explicit
sole-supplier statement), never by model opinion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Edge types (3a scope).
CUSTOMER_CONCENTRATION = "customer_concentration"
SOLE_SUPPLIER = "sole_supplier"
REGULATORY_PRECEDENT = "regulatory_precedent"

# A single disclosed customer must be >= this % of revenue to be material.
CONCENTRATION_MIN_PCT = 20.0

# Customer-concentration language: a percentage near customer+revenue words.
# NOTE (3a-F guard 1): accounts receivable is deliberately NOT a revenue word —
# AR-basis tables were the single biggest source of false edges in 3b grading.
_PCT_RE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s?%")
_CUSTOMER_RE = re.compile(r"\b(customer|client)s?\b", re.IGNORECASE)
_REVENUE_RE = re.compile(
    r"\b(revenues?|net sales|total sales|gross product revenues?|sales)\b", re.IGNORECASE
)

_SOLE_SUPPLIER_RE = re.compile(
    r"\b(sole[\s-]+(source\s+)?(supplier|provider|manufacturer|vendor)"
    r"|single[\s-]+source\s+(supplier|provider|manufacturer|vendor)"
    r"|only\s+(qualified\s+)?(supplier|source)\s+of)\b",
    re.IGNORECASE,
)

_REGULATORY_RE = re.compile(
    r"\b(consent\s+decree|deferred\s+prosecution\s+agreement"
    r"|corporate\s+integrity\s+agreement"
    r"|settlement\s+(agreement\s+)?with\s+the\s+(SEC|DOJ|FTC|FDA|EPA|CFTC|OCC|CFPB))\b",
    re.IGNORECASE,
)

# Window (chars) around a match expanded to sentence-ish boundaries.
_WINDOW = 420
_MAX_PASSAGE_CHARS = 1200


@dataclass(frozen=True)
class CandidatePassage:
    symbol: str
    edge_type_hint: str
    passage: str
    max_pct: float | None  # largest % mentioned in the passage, if any


def _expand_to_sentences(text: str, start: int, end: int) -> str:
    lo = max(0, start - _WINDOW)
    hi = min(len(text), end + _WINDOW)
    chunk = text[lo:hi]
    # Trim leading partial sentence.
    first_stop = chunk.find(". ")
    if 0 <= first_stop < _WINDOW and lo > 0:
        chunk = chunk[first_stop + 2 :]
    # Trim trailing partial sentence.
    last_stop = chunk.rfind(". ")
    if last_stop > len(chunk) - _WINDOW and hi < len(text):
        chunk = chunk[: last_stop + 1]
    return " ".join(chunk.split())[:_MAX_PASSAGE_CHARS]


def _max_pct(passage: str) -> float | None:
    values = [float(m.group(1)) for m in _PCT_RE.finditer(passage)]
    values = [v for v in values if 0 < v < 100]
    return max(values) if values else None


def find_candidate_passages(symbol: str, text: str) -> list[CandidatePassage]:
    """Scan filing text for candidate edge passages (deduped, ordered).

    3a-F: guard pre-filters run here too (cheaply, before any LLM spend) —
    concentration candidates need at least one revenue-certified %>=20, and
    regulatory candidates need operative-decree language. The authoritative
    application of all guards is in `confirm.validate_llm_edge`.
    """
    from stockbot.bots.frontier.guards import operative_decree, revenue_basis_certified

    out: list[CandidatePassage] = []
    seen: set[str] = set()

    def add(hint: str, start: int, end: int, *, require_pct: float | None = None) -> None:
        passage = _expand_to_sentences(text, start, end)
        pct = _max_pct(passage)
        if require_pct is not None and (pct is None or pct < require_pct):
            return
        if hint == CUSTOMER_CONCENTRATION and not any(
            revenue_basis_certified(passage, float(p.group(1)))
            for p in _PCT_RE.finditer(passage)
            if CONCENTRATION_MIN_PCT <= float(p.group(1)) < 100
        ):
            return  # guard 1: every big % in this passage is AR-basis or basis-less
        if hint == REGULATORY_PRECEDENT and not operative_decree(passage):
            return  # guard 3: proposed/dead/third-party decree
        key = passage[:160]
        if key in seen:
            return
        seen.add(key)
        out.append(
            CandidatePassage(symbol=symbol.upper(), edge_type_hint=hint, passage=passage, max_pct=pct)
        )

    # Customer concentration: % token with customer + revenue words nearby.
    for m in _PCT_RE.finditer(text):
        value = float(m.group(1))
        if value < CONCENTRATION_MIN_PCT or value >= 100:
            continue
        lo = max(0, m.start() - 260)
        hi = min(len(text), m.end() + 260)
        context = text[lo:hi]
        if _CUSTOMER_RE.search(context) and _REVENUE_RE.search(context):
            add(CUSTOMER_CONCENTRATION, m.start(), m.end(), require_pct=CONCENTRATION_MIN_PCT)

    for m in _SOLE_SUPPLIER_RE.finditer(text):
        add(SOLE_SUPPLIER, m.start(), m.end())

    for m in _REGULATORY_RE.finditer(text):
        add(REGULATORY_PRECEDENT, m.start(), m.end())

    return out

"""Shot 7 (Agent B) — edge dedupe.

Round-4 grading (`reports/3b-grading-4.md`) found duplicate edges inflating the
named pool and the precision sample:
- **EVTC** — `Popular` (resolved BPOP) vs its subsidiary `Banco Popular`, the
  same 29% concentration, same filing (parent/subsidiary).
- **GLIBA / GLIBK** — Liberty Broadband dual share classes, ONE 10-K, identical
  FCC consent-decree quote (same accession, two filer tickers).
- **CENT / Home Depot** — the builder flagged an identical passage twice.

The schema's UNIQUE(from_symbol, accession_number, edge_type,
counterparty_canonical) only stops exact dupes within one filer's filing. These
slip through because they cross filer tickers (dual class), use counterparty
name variants (parent vs subsidiary), or repeat a passage. This module collapses
them deterministically and records EVERY merge so the change in edge counts is
auditable.

Four conservative merge rules (union-find; a pair may be linked by more than one
— the highest-priority rule is recorded):
  D1 same_relationship      — identical (norm source, type, counterparty_canonical)
  D3 dual_class             — same filing (accession) + same (type, counterparty),
                              different filer tickers (share classes)
  D2 near_identical_quote   — same (source, type), near-identical normalized quote
  D4 parent_subsidiary      — same (source, type), one counterparty name a token
                              subset of the other, with matching magnitude or an
                              explicit subsidiary/parent mention

Pure functions over edge dicts (as returned by `StateStore.get_frontier_edges`);
the re-filter applies the survivors by deleting the merged-away edge ids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from stockbot.bots.frontier.resolve import normalize_company_name

# Rule priority (lower = recorded in preference when a pair is multiply-linked).
_RULE_PRIORITY = {
    "d1_same_relationship": 0,
    "d3_dual_class": 1,
    "d2_near_identical_quote": 2,
    "d4_parent_subsidiary": 3,
}

# Normalized-quote similarity threshold for D2 ("near-identical").
NEAR_IDENTICAL_QUOTE_RATIO = 0.92

_SUBSIDIARY_RE = re.compile(
    r"\b(?:wholly[\s-]owned\s+)?subsidiary\s+of\b|\bparent\s+(?:company|of)\b"
    r"|\bdivision\s+of\b|\ban?\s+affiliate\s+of\b",
    re.IGNORECASE,
)


@dataclass
class MergeRecord:
    rule: str
    kept_edge_id: str
    dropped_edge_id: str
    from_symbol: str
    edge_type: str
    kept_counterparty: str
    dropped_counterparty: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "kept_edge_id": self.kept_edge_id,
            "dropped_edge_id": self.dropped_edge_id,
            "from_symbol": self.from_symbol,
            "edge_type": self.edge_type,
            "kept_counterparty": self.kept_counterparty,
            "dropped_counterparty": self.dropped_counterparty,
            "detail": self.detail,
        }


def _norm_quote(quote: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (quote or "").lower()).strip()


def _quote_tokens(quote: str) -> list[str]:
    return _norm_quote(quote).split()


def _quote_similarity(a: str, b: str) -> float:
    """Token Jaccard, with substring containment treated as identical.

    Containment handles truncation variants (one passage a window of the other).
    """
    ta, tb = _norm_quote(a), _norm_quote(b)
    if not ta or not tb:
        return 0.0
    if ta == tb or ta in tb or tb in ta:
        return 1.0
    sa, sb = set(ta.split()), set(tb.split())
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _canonical_tokens(edge: dict[str, Any]) -> frozenset[str]:
    canon = edge.get("counterparty_canonical") or normalize_company_name(
        edge.get("counterparty_name", "")
    )
    return frozenset(t for t in canon.split() if t)


def _is_token_subset(a: frozenset[str], b: frozenset[str]) -> bool:
    """True if one non-empty set is a PROPER subset of the other."""
    if not a or not b or a == b:
        return False
    return a < b or b < a


class _UnionFind:
    def __init__(self, ids: list[str]) -> None:
        self._parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _survivor_key(edge: dict[str, Any]) -> tuple:
    """Lower = preferred survivor: resolved ticker, named, has magnitude,
    longer quote, then stable edge_id."""
    return (
        0 if edge.get("counterparty_symbol") else 1,
        0 if edge.get("named") else 1,
        0 if edge.get("magnitude_pct") is not None else 1,
        -len(edge.get("quoted_passage") or ""),
        edge["edge_id"],
    )


def _collect_pairs(edges: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """All candidate merge pairs as (edge_id_a, edge_id_b, rule, detail)."""
    pairs: list[tuple[str, str, str, str]] = []
    by_id = {e["edge_id"]: e for e in edges}

    def src(e: dict[str, Any]) -> str:
        return (e.get("from_symbol") or "").upper()

    # D1 — identical (norm source, type, counterparty_canonical).
    d1: dict[tuple[str, str, str], list[str]] = {}
    for e in edges:
        key = (src(e), e["edge_type"], (e.get("counterparty_canonical") or "").upper())
        d1.setdefault(key, []).append(e["edge_id"])
    for key, ids in d1.items():
        for other in ids[1:]:
            pairs.append((ids[0], other, "d1_same_relationship", f"same {key[1]}/{key[2]}"))

    # D3 — dual share classes: same filing (accession) + same (type, canonical),
    # different filer tickers.
    d3: dict[tuple[str, str, str], list[str]] = {}
    for e in edges:
        key = (
            e.get("accession_number") or "",
            e["edge_type"],
            (e.get("counterparty_canonical") or "").upper(),
        )
        d3.setdefault(key, []).append(e["edge_id"])
    for ids in d3.values():
        sources = {src(by_id[i]) for i in ids}
        if len(ids) > 1 and len(sources) > 1:
            for other in ids[1:]:
                pairs.append(
                    (ids[0], other, "d3_dual_class", f"shared filing {by_id[ids[0]].get('accession_number')}")
                )

    # Same (source, type) buckets for D2 / D4.
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in edges:
        buckets.setdefault((src(e), e["edge_type"]), []).append(e)

    for group in buckets.values():
        n = len(group)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = group[i], group[j]
                # D2 — near-identical quote.
                sim = _quote_similarity(a.get("quoted_passage", ""), b.get("quoted_passage", ""))
                if sim >= NEAR_IDENTICAL_QUOTE_RATIO:
                    pairs.append(
                        (a["edge_id"], b["edge_id"], "d2_near_identical_quote", f"quote sim={sim:.2f}")
                    )
                    continue
                # D4 — parent/subsidiary by counterparty-name containment, with a
                # corroborating signal (equal magnitude, or explicit subsidiary/
                # parent wording in either quote) to stay conservative.
                if _is_token_subset(_canonical_tokens(a), _canonical_tokens(b)):
                    mags_match = (
                        a.get("magnitude_pct") is not None
                        and a.get("magnitude_pct") == b.get("magnitude_pct")
                    )
                    sub_mention = bool(
                        _SUBSIDIARY_RE.search(a.get("quoted_passage", ""))
                        or _SUBSIDIARY_RE.search(b.get("quoted_passage", ""))
                    )
                    if mags_match or sub_mention:
                        why = "equal magnitude" if mags_match else "subsidiary/parent wording"
                        pairs.append(
                            (a["edge_id"], b["edge_id"], "d4_parent_subsidiary", f"name containment + {why}")
                        )
    return pairs


def dedupe_edges(
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[MergeRecord]]:
    """Collapse duplicate edges. Returns (survivors, merge_records).

    Deterministic: pairs are unioned, each component keeps one canonical
    survivor (resolved/named/magnitude/longer-quote/stable-id), and every
    dropped edge is reported with the highest-priority rule that linked it.
    """
    if not edges:
        return [], []
    by_id = {e["edge_id"]: e for e in edges}
    uf = _UnionFind(list(by_id))
    pairs = _collect_pairs(edges)
    for a, b, _rule, _detail in pairs:
        uf.union(a, b)

    # Best (rule, detail) per unordered edge pair, by rule priority.
    best_pair: dict[frozenset[str], tuple[str, str]] = {}
    for a, b, rule, detail in pairs:
        key = frozenset((a, b))
        cur = best_pair.get(key)
        if cur is None or _RULE_PRIORITY[rule] < _RULE_PRIORITY[cur[0]]:
            best_pair[key] = (rule, detail)

    components: dict[str, list[str]] = {}
    for eid in by_id:
        components.setdefault(uf.find(eid), []).append(eid)

    survivors: list[dict[str, Any]] = []
    merges: list[MergeRecord] = []
    for member_ids in components.values():
        members = [by_id[i] for i in member_ids]
        members.sort(key=_survivor_key)
        keeper = members[0]
        survivors.append(keeper)
        for dropped in members[1:]:
            rule, detail = _best_rule_between(keeper, dropped, member_ids, best_pair)
            merges.append(
                MergeRecord(
                    rule=rule,
                    kept_edge_id=keeper["edge_id"],
                    dropped_edge_id=dropped["edge_id"],
                    from_symbol=(keeper.get("from_symbol") or "").upper(),
                    edge_type=keeper["edge_type"],
                    kept_counterparty=keeper.get("counterparty_name", ""),
                    dropped_counterparty=dropped.get("counterparty_name", ""),
                    detail=detail,
                )
            )
    survivors.sort(key=lambda e: e["edge_id"])
    merges.sort(key=lambda m: (m.from_symbol, m.edge_type, m.dropped_edge_id))
    return survivors, merges


def _best_rule_between(
    keeper: dict[str, Any],
    dropped: dict[str, Any],
    member_ids: list[str],
    best_pair: dict[frozenset[str], tuple[str, str]],
) -> tuple[str, str]:
    """Reason a dropped edge merged into the keeper: prefer the direct pair;
    otherwise the highest-priority rule among pairs touching the dropped edge in
    the component (it joined transitively)."""
    direct = best_pair.get(frozenset((keeper["edge_id"], dropped["edge_id"])))
    if direct is not None:
        return direct
    candidates = [
        rd
        for key, rd in best_pair.items()
        if dropped["edge_id"] in key and key <= set(member_ids)
    ]
    if candidates:
        return min(candidates, key=lambda rd: _RULE_PRIORITY[rd[0]])
    return ("d1_same_relationship", "transitive merge")

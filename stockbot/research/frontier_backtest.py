"""Ticket F-0 + 3c measurement — Frontier propagation signal-frequency reality
check and the PIT-correct, sector-neutral sign-accuracy backtest.

This is the gate measurement (the gate is judged on this + forward paper, not a
build-time pass/fail). It imports the SAME core semantics the live strategy
uses (`stockbot.bots.frontier.propagation`): the direction map, the PIT
"edge live as-of" selection, the anchor-shock test, and the sector
neutralization. There is therefore no seam between what we measure and what we
trade — that is the whole point of building 3c/3d as one unit.

Data sources (all real, all PIT-honest):
- The live graph: `frontier_edges` in the state DB (the 106-named-edge survivor
  set; only the 22 observable/resolved-counterparty tradeable edges propagate).
- Filer + sector-peer daily prices: the expanded-universe `prices.csv`.
- Anchor daily prices: fetched from Tiingo into a cached CSV (the large-cap
  anchors are NOT in the $300M-$3B universe — see `ingest_anchor_prices`).

Reproduction:
    python -m stockbot.research.frontier_backtest \
        --db data/state.db \
        --universe-prices data/real_expanded/prices.csv \
        --universe-metadata stockbot/data/universe_expanded.metadata.json \
        --out reports/shot8-f0-backtest.json
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from stockbot.bots.frontier.propagation import (
    ANCHOR_SHOCK_THRESHOLD,
    ANCHOR_UP_SHOCK_THRESHOLD,
    DIRECTION_MAP,
    EDGE_MAX_AGE_DAYS,
    HOLD_TRADING_DAYS,
    SECTOR_BENCHMARK_MAX_PEERS,
    UP_DIRECTION_MAP,
    is_anchor_shock,
    is_anchor_up_shock,
    predicted_direction,
    sector_benchmark_peers,
    sector_neutral_excess,
    sign_is_correct,
    trade_type_for,
    up_predicted_direction,
    up_trade_type_for,
)
from stockbot.config import load_env
from stockbot.core.state.store import StateStore
from stockbot.data.real_loader import CachedHTTP, load_tiingo_prices

logger = logging.getLogger(__name__)

DEFAULT_ANCHOR_PRICES = "data/frontier_anchors/prices.csv"
DEFAULT_ANCHOR_CACHE = "data/frontier_anchors/cache"
PEER_LOOKUP_TOLERANCE_DAYS = 5


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@dataclass
class PriceSeries:
    """A symbol's sorted (date, price) history with O(log n) as-of lookup."""

    dates: list[date] = field(default_factory=list)
    prices: list[float] = field(default_factory=list)

    def add(self, d: date, p: float) -> None:
        self.dates.append(d)
        self.prices.append(p)

    def finalize(self) -> None:
        order = sorted(range(len(self.dates)), key=lambda i: self.dates[i])
        self.dates = [self.dates[i] for i in order]
        self.prices = [self.prices[i] for i in order]

    def price_on_or_before(self, target: date, tol_days: int = PEER_LOOKUP_TOLERANCE_DAYS) -> float | None:
        """Price exactly on `target`, else the most recent prior within tol."""
        i = bisect.bisect_right(self.dates, target) - 1
        if i < 0:
            return None
        if (target - self.dates[i]).days > tol_days:
            return None
        return self.prices[i]

    def first_after(self, target: date) -> tuple[date, float] | None:
        """(date, price) of the first trading day strictly after `target`."""
        i = bisect.bisect_right(self.dates, target)
        if i >= len(self.dates):
            return None
        return self.dates[i], self.prices[i]

    def nth_after(self, target: date, n: int) -> tuple[date, float] | None:
        """(date, price) of the n-th trading day strictly after `target`."""
        i = bisect.bisect_right(self.dates, target) + (n - 1)
        if i >= len(self.dates):
            return None
        return self.dates[i], self.prices[i]


def load_price_series(prices_csv: str | Path, symbols: set[str] | None = None) -> dict[str, PriceSeries]:
    """Load per-symbol daily close series from a feed-format prices.csv."""
    series: dict[str, PriceSeries] = defaultdict(PriceSeries)
    with Path(prices_csv).open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = str(row["symbol"]).upper()
            if symbols is not None and sym not in symbols:
                continue
            try:
                price = float(row["price"])
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            d = datetime.fromisoformat(row["timestamp"]).date()
            series[sym].add(d, price)
    for s in series.values():
        s.finalize()
    return dict(series)


def load_sector_map(metadata_json: str | Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return (symbol -> sector, sector -> [symbols]) from the universe metadata."""
    meta = json.loads(Path(metadata_json).read_text(encoding="utf-8"))
    symbol_sector: dict[str, str] = {}
    sector_members: dict[str, list[str]] = defaultdict(list)
    for row in meta.get("symbols", []):
        sym = str(row["symbol"]).upper()
        sector = str(row.get("sector") or "")
        symbol_sector[sym] = sector
        sector_members[sector].append(sym)
    return symbol_sector, dict(sector_members)


def load_tradeable_edges(db_path: str | Path) -> list[dict[str, Any]]:
    """All observable (resolved-ticker) edges of a tradeable type from the graph."""
    store = StateStore(db_path)
    out: list[dict[str, Any]] = []
    for etype in DIRECTION_MAP:
        for e in store.get_frontier_edges(edge_type=etype, named_only=True):
            if e.get("counterparty_symbol"):
                out.append(e)
    return out


def distinct_anchors(edges: list[dict[str, Any]]) -> list[str]:
    return sorted({str(e["counterparty_symbol"]).upper() for e in edges if e.get("counterparty_symbol")})


def ingest_anchor_prices(
    anchors: list[str],
    *,
    start: date,
    end: date,
    out_csv: str | Path = DEFAULT_ANCHOR_PRICES,
    cache_dir: str | Path = DEFAULT_ANCHOR_CACHE,
    tiingo_api_key: str,
    offline: bool = False,
) -> Path:
    """Fetch (cached) daily anchor prices into a feed-format CSV.

    The large-cap anchors are absent from the mid/small-cap universe, so their
    history must be pulled separately. Cached on disk so the backtest and tests
    reproduce offline.
    """
    client = CachedHTTP(cache_dir=cache_dir, offline=offline)
    rows: list[dict[str, Any]] = []
    for anchor in anchors:
        try:
            rows.extend(load_tiingo_prices(client, anchor, start, end, tiingo_api_key))
        except Exception as exc:  # noqa: BLE001 - per-anchor isolation
            logger.warning("anchor price fetch failed for %s: %s", anchor, exc)
    rows.sort(key=lambda r: (r["timestamp"], r["symbol"]))
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume", "listed_at", "delisted_at"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return out


# ---------------------------------------------------------------------------
# Signal enumeration (shared by F-0 and the backtest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    anchor: str
    shock_date: date
    move_pct: float
    filer: str
    edge_type: str
    direction: str
    filed_at: str
    magnitude_pct: float | None


def enumerate_anchor_shocks(anchor_series: dict[str, PriceSeries]) -> dict[str, list[tuple[date, float]]]:
    """Per anchor: every pre-registered down-shock day (date, move_pct)."""
    shocks: dict[str, list[tuple[date, float]]] = {}
    for anchor, s in anchor_series.items():
        hits: list[tuple[date, float]] = []
        for i in range(1, len(s.prices)):
            prev, cur = s.prices[i - 1], s.prices[i]
            if is_anchor_shock(prev, cur):
                hits.append((s.dates[i], (cur - prev) / prev))
        if hits:
            shocks[anchor] = hits
    return shocks


def enumerate_signals(
    edges: list[dict[str, Any]],
    anchor_series: dict[str, PriceSeries],
) -> list[Signal]:
    """Every PIT-valid, observable-counterparty propagation signal in the data.

    A signal exists when an anchor has a down-shock on day T AND a tradeable
    edge from that anchor is PIT-live (filed_at <= T <= filed_at + 18mo).
    """
    by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in edges:
        by_anchor[str(e["counterparty_symbol"]).upper()].append(e)
    shocks = enumerate_anchor_shocks(anchor_series)
    out: list[Signal] = []
    for anchor, hits in shocks.items():
        linked = by_anchor.get(anchor, [])
        if not linked:
            continue
        for shock_date, move in hits:
            for e in linked:
                filed_raw = str(e.get("filed_at", ""))[:10]
                try:
                    filed = date.fromisoformat(filed_raw)
                except ValueError:
                    continue
                # PIT: edge live only filed <= shock_date <= filed + 18mo.
                if filed > shock_date:
                    continue
                if (shock_date - filed).days > EDGE_MAX_AGE_DAYS:
                    continue
                direction = predicted_direction(e["edge_type"])
                if direction is None:
                    continue
                out.append(
                    Signal(
                        anchor=anchor,
                        shock_date=shock_date,
                        move_pct=move,
                        filer=str(e["from_symbol"]).upper(),
                        edge_type=e["edge_type"],
                        direction=direction,
                        filed_at=filed.isoformat(),
                        magnitude_pct=e.get("magnitude_pct"),
                    )
                )
    out.sort(key=lambda s: (s.shock_date, s.anchor, s.filer))
    return out


def enumerate_up_anchor_shocks(
    anchor_series: dict[str, PriceSeries],
) -> dict[str, list[tuple[date, float]]]:
    """9e — per anchor: every pre-registered UP-shock day (date, move_pct)."""
    shocks: dict[str, list[tuple[date, float]]] = {}
    for anchor, s in anchor_series.items():
        hits: list[tuple[date, float]] = []
        for i in range(1, len(s.prices)):
            prev, cur = s.prices[i - 1], s.prices[i]
            if is_anchor_up_shock(prev, cur):
                hits.append((s.dates[i], (cur - prev) / prev))
        if hits:
            shocks[anchor] = hits
    return shocks


def enumerate_up_signals(
    edges: list[dict[str, Any]],
    anchor_series: dict[str, PriceSeries],
) -> list[Signal]:
    """9e — every PIT-valid, observable-counterparty UP propagation signal.

    Mirror of ``enumerate_signals`` for POSITIVE anchor shocks: an up-signal
    exists when an anchor RISES >= the up threshold on day T AND a tradeable
    edge from that anchor is PIT-live. Same PIT discipline; direction = up.
    """
    by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in edges:
        by_anchor[str(e["counterparty_symbol"]).upper()].append(e)
    shocks = enumerate_up_anchor_shocks(anchor_series)
    out: list[Signal] = []
    for anchor, hits in shocks.items():
        linked = by_anchor.get(anchor, [])
        if not linked:
            continue
        for shock_date, move in hits:
            for e in linked:
                filed_raw = str(e.get("filed_at", ""))[:10]
                try:
                    filed = date.fromisoformat(filed_raw)
                except ValueError:
                    continue
                if filed > shock_date:
                    continue
                if (shock_date - filed).days > EDGE_MAX_AGE_DAYS:
                    continue
                direction = up_predicted_direction(e["edge_type"])
                if direction is None:
                    continue
                out.append(
                    Signal(
                        anchor=anchor,
                        shock_date=shock_date,
                        move_pct=move,
                        filer=str(e["from_symbol"]).upper(),
                        edge_type=e["edge_type"],
                        direction=direction,
                        filed_at=filed.isoformat(),
                        magnitude_pct=e.get("magnitude_pct"),
                    )
                )
    out.sort(key=lambda s: (s.shock_date, s.anchor, s.filer))
    return out


# ---------------------------------------------------------------------------
# F-0 — signal-frequency reality check
# ---------------------------------------------------------------------------


def f0_report(
    signals: list[Signal],
    anchor_series: dict[str, PriceSeries],
    *,
    threshold: float = ANCHOR_SHOCK_THRESHOLD,
) -> dict[str, Any]:
    """Signals per bucket per year + observable-counterparty counts."""
    per_year_bucket: dict[tuple[int, str], int] = defaultdict(int)
    per_bucket: dict[str, int] = defaultdict(int)
    years: set[int] = set()
    for s in signals:
        y = s.shock_date.year
        years.add(y)
        per_year_bucket[(y, s.edge_type)] += 1
        per_bucket[s.edge_type] += 1
    # Span of the anchor price history (the observation window).
    all_dates = [d for s in anchor_series.values() for d in s.dates]
    span_start = min(all_dates).isoformat() if all_dates else None
    span_end = max(all_dates).isoformat() if all_dates else None
    return {
        "total_signals": len(signals),
        "per_bucket": dict(per_bucket),
        "per_year_bucket": {f"{y}:{b}": n for (y, b), n in sorted(per_year_bucket.items())},
        "years": sorted(years),
        "observation_window": {"start": span_start, "end": span_end},
        "distinct_anchors_with_shocks": len(
            {s.anchor for s in signals}
        ),
        "anchor_shock_threshold": threshold,
    }


# ---------------------------------------------------------------------------
# Backtest — PIT-correct, sector-neutral sign-accuracy
# ---------------------------------------------------------------------------


@dataclass
class Resolved:
    signal: Signal
    entry_date: date
    exit_date: date
    filer_return: float
    excess_return: float
    sector_neutral: bool
    peer_count: int
    outcome: bool


def backtest_signals(
    signals: list[Signal],
    universe_series: dict[str, PriceSeries],
    symbol_sector: dict[str, str],
    sector_members: dict[str, list[str]],
    *,
    hold_trading_days: int = HOLD_TRADING_DAYS,
    max_peers: int = SECTOR_BENCHMARK_MAX_PEERS,
) -> list[Resolved]:
    """Enter T+1, hold N trading days, resolve on sector-neutral excess sign."""
    resolved: list[Resolved] = []
    for sig in signals:
        filer_series = universe_series.get(sig.filer)
        if filer_series is None:
            continue
        entry = filer_series.first_after(sig.shock_date)
        if entry is None:
            continue
        entry_date, entry_price = entry
        exit_pt = filer_series.nth_after(sig.shock_date, 1 + hold_trading_days)
        if exit_pt is None:
            continue
        exit_date, exit_price = exit_pt
        if entry_price <= 0 or exit_price <= 0:
            continue
        filer_return = (exit_price - entry_price) / entry_price

        sector = symbol_sector.get(sig.filer, "")
        peers = sector_benchmark_peers(sig.filer, sector_members.get(sector, []), max_peers=max_peers)
        peer_returns: list[float] = []
        for p in peers:
            ps = universe_series.get(p)
            if ps is None:
                continue
            p_entry = ps.price_on_or_before(entry_date)
            p_exit = ps.price_on_or_before(exit_date)
            if p_entry and p_exit and p_entry > 0:
                peer_returns.append((p_exit - p_entry) / p_entry)
        excess = sector_neutral_excess(filer_return, peer_returns)
        if excess is None:
            excess = filer_return
            sector_neutral = False
        else:
            sector_neutral = True
        resolved.append(
            Resolved(
                signal=sig,
                entry_date=entry_date,
                exit_date=exit_date,
                filer_return=filer_return,
                excess_return=excess,
                sector_neutral=sector_neutral,
                peer_count=len(peer_returns),
                outcome=sign_is_correct(sig.direction, excess),
            )
        )
    return resolved


def sign_accuracy_by_bucket(
    resolved: list[Resolved], *, up: bool = False
) -> dict[str, dict[str, Any]]:
    """Per edge-type bucket: n, hits, sign-accuracy, mean excess.

    ``up=True`` (9e) suffixes the bucket key with ``_up`` and uses the up-side
    trade-type so the up and down measurements are reported side-by-side but
    NEVER commingled. Default (down) output is unchanged.
    """
    by_bucket: dict[str, list[Resolved]] = defaultdict(list)
    for r in resolved:
        key = f"{r.signal.edge_type}_up" if up else r.signal.edge_type
        by_bucket[key].append(r)
    out: dict[str, dict[str, Any]] = {}
    for bucket, rs in sorted(by_bucket.items()):
        n = len(rs)
        hits = sum(1 for r in rs if r.outcome)
        edge_type = bucket[:-3] if up else bucket
        out[bucket] = {
            "trade_type": up_trade_type_for(edge_type) if up else trade_type_for(edge_type),
            "n": n,
            "hits": hits,
            "sign_accuracy": (hits / n) if n else None,
            "mean_excess_return": (sum(r.excess_return for r in rs) / n) if n else None,
            "mean_filer_return": (sum(r.filer_return for r in rs) / n) if n else None,
            "sector_neutral_n": sum(1 for r in rs if r.sector_neutral),
        }
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(
    *,
    db_path: str | Path,
    universe_prices: str | Path,
    universe_metadata: str | Path,
    anchor_prices: str | Path = DEFAULT_ANCHOR_PRICES,
    ingest: bool = False,
    offline: bool = False,
) -> dict[str, Any]:
    edges = load_tradeable_edges(db_path)
    anchors = distinct_anchors(edges)

    if ingest:
        env = load_env()
        ingest_anchor_prices(
            anchors,
            start=date(2025, 1, 1),
            end=date(2026, 6, 9),
            out_csv=anchor_prices,
            tiingo_api_key=env.tiingo_api_key,
            offline=offline,
        )

    anchor_series = load_price_series(anchor_prices, symbols=set(anchors))
    symbol_sector, sector_members = load_sector_map(universe_metadata)

    filers = {str(e["from_symbol"]).upper() for e in edges}
    needed_sectors = {symbol_sector.get(f, "") for f in filers}
    peer_symbols: set[str] = set(filers)
    for sec in needed_sectors:
        peer_symbols.update(s.upper() for s in sector_members.get(sec, []))
    universe_series = load_price_series(universe_prices, symbols=peer_symbols)

    signals = enumerate_signals(edges, anchor_series)
    f0 = f0_report(signals, anchor_series)
    resolved = backtest_signals(signals, universe_series, symbol_sector, sector_members)
    buckets = sign_accuracy_by_bucket(resolved)

    # 9e — the additive UP side, measured the SAME way (same PIT, same
    # sector-neutralization), reported beside the down side and never commingled.
    up_signals = enumerate_up_signals(edges, anchor_series)
    up_f0 = f0_report(up_signals, anchor_series, threshold=ANCHOR_UP_SHOCK_THRESHOLD)
    up_resolved = backtest_signals(up_signals, universe_series, symbol_sector, sector_members)
    up_buckets = sign_accuracy_by_bucket(up_resolved, up=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph": {
            "tradeable_edges": len(edges),
            "distinct_anchors": anchors,
            "filers": sorted(filers),
        },
        "pre_registered": {
            "anchor_shock_threshold": ANCHOR_SHOCK_THRESHOLD,
            "anchor_shock_direction": "down",
            "direction_map": DIRECTION_MAP,
            "hold_trading_days": HOLD_TRADING_DAYS,
            "entry_offset_trading_days": 1,
            "sector_benchmark_max_peers": SECTOR_BENCHMARK_MAX_PEERS,
            "edge_max_age_days": EDGE_MAX_AGE_DAYS,
        },
        "pre_registered_up": {  # 9e — frozen separately, never edits the down block
            "anchor_up_shock_threshold": ANCHOR_UP_SHOCK_THRESHOLD,
            "anchor_shock_direction": "up",
            "up_direction_map": UP_DIRECTION_MAP,
            "tradeable": "long BUY (long-only cash account); ≤3% equity sizing",
            "buckets": ["frontier:customer_concentration_up", "frontier:sole_supplier_up"],
        },
        "up_f0_signal_frequency": up_f0,
        "up_backtest": {
            "resolved_signals": len(up_resolved),
            "by_bucket_sign_accuracy": up_buckets,
            "bar": 0.55,
            "detail": [
                {
                    "shock_date": r.signal.shock_date.isoformat(),
                    "anchor": r.signal.anchor,
                    "anchor_move_pct": r.signal.move_pct,
                    "filer": r.signal.filer,
                    "edge_type": r.signal.edge_type,
                    "direction": r.signal.direction,
                    "entry_date": r.entry_date.isoformat(),
                    "exit_date": r.exit_date.isoformat(),
                    "filer_return": r.filer_return,
                    "excess_return": r.excess_return,
                    "sector_neutral": r.sector_neutral,
                    "peer_count": r.peer_count,
                    "outcome": r.outcome,
                }
                for r in up_resolved
            ],
        },
        "f0_signal_frequency": f0,
        "backtest": {
            "resolved_signals": len(resolved),
            "by_bucket_sign_accuracy": buckets,
            "bar": 0.55,
            "detail": [
                {
                    "shock_date": r.signal.shock_date.isoformat(),
                    "anchor": r.signal.anchor,
                    "anchor_move_pct": r.signal.move_pct,
                    "filer": r.signal.filer,
                    "edge_type": r.signal.edge_type,
                    "direction": r.signal.direction,
                    "entry_date": r.entry_date.isoformat(),
                    "exit_date": r.exit_date.isoformat(),
                    "filer_return": r.filer_return,
                    "excess_return": r.excess_return,
                    "sector_neutral": r.sector_neutral,
                    "peer_count": r.peer_count,
                    "outcome": r.outcome,
                }
                for r in resolved
            ],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frontier F-0 + sign-accuracy backtest")
    parser.add_argument("--db", default="data/state.db")
    parser.add_argument("--universe-prices", default="data/real_expanded/prices.csv")
    parser.add_argument("--universe-metadata", default="stockbot/data/universe_expanded.metadata.json")
    parser.add_argument("--anchor-prices", default=DEFAULT_ANCHOR_PRICES)
    parser.add_argument("--ingest", action="store_true", help="Fetch anchor prices first (network)")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--out", default="reports/shot8-f0-backtest.json")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    payload = run(
        db_path=args.db,
        universe_prices=args.universe_prices,
        universe_metadata=args.universe_metadata,
        anchor_prices=args.anchor_prices,
        ingest=args.ingest,
        offline=args.offline,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("== DOWN side ==")
    print(json.dumps(payload["f0_signal_frequency"], indent=2))
    print(json.dumps(payload["backtest"]["by_bucket_sign_accuracy"], indent=2))
    print("== UP side (9e) ==")
    print(json.dumps(payload["up_f0_signal_frequency"], indent=2))
    print(json.dumps(payload["up_backtest"]["by_bucket_sign_accuracy"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

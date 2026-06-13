"""Frontier Engine Stage 1 pipeline (ticket 3a): build the typed-edge graph.

For each universe symbol: fetch the latest 10-K (cached), regex-locate
candidate passages, Haiku-confirm/type/extract (hard $5 cap), conservatively
resolve counterparty tickers, and store edges in `StateStore.frontier_edges`.

Idempotent & resumable: a sidecar `scanned_accessions.json` in the cache dir
records which (symbol, accession) pairs were already extracted, so re-runs
spend zero LLM money on unchanged filings. NO trading code touches this graph.

Run: python -m stockbot.bots.frontier.build_graph
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from stockbot.bots.frontier.candidates import find_candidate_passages
from stockbot.bots.frontier.confirm import EdgeConfirmer, HaikuEdgeConfirmer, SpendCapExceeded
from stockbot.bots.frontier.guards import canonical_counterparty
from stockbot.bots.frontier.filings import fetch_filing_text, latest_annual_filing
from stockbot.bots.frontier.resolve import load_name_resolver, resolve_counterparty
from stockbot.config import load_env
from stockbot.core.state.store import StateStore
from stockbot.data.real_loader import CachedHTTP, load_sec_ticker_map
from stockbot.data.universe import DEFAULT_UNIVERSE_PATH, load_universe

logger = logging.getLogger(__name__)


@dataclass
class GraphBuildStats:
    filings_scanned: int = 0
    symbols_no_cik: int = 0
    symbols_no_10k: int = 0
    symbols_skipped_already_scanned: int = 0
    candidate_passages: int = 0
    confirmed_edges: int = 0
    edges_inserted: int = 0
    edges_by_type: dict[str, int] = field(default_factory=dict)
    named_edges: int = 0
    anonymous_edges: int = 0
    resolved_tickers: int = 0
    stopped_by_spend_cap: bool = False
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_requests: int = 0
    llm_spend_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "filings_scanned": self.filings_scanned,
            "symbols_no_cik": self.symbols_no_cik,
            "symbols_no_10k": self.symbols_no_10k,
            "symbols_skipped_already_scanned": self.symbols_skipped_already_scanned,
            "candidate_passages": self.candidate_passages,
            "confirmed_edges": self.confirmed_edges,
            "edges_inserted": self.edges_inserted,
            "edges_by_type": dict(sorted(self.edges_by_type.items())),
            "named_edges": self.named_edges,
            "anonymous_edges": self.anonymous_edges,
            "resolved_tickers": self.resolved_tickers,
            "stopped_by_spend_cap": self.stopped_by_spend_cap,
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "llm_requests": self.llm_requests,
            "llm_spend_usd": round(self.llm_spend_usd, 4),
        }


def _load_scan_state(path: Path) -> dict[str, str]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def build_graph(
    *,
    store: StateStore,
    client: CachedHTTP,
    confirmer: EdgeConfirmer,
    sec_user_agent: str,
    universe_path: str | Path = DEFAULT_UNIVERSE_PATH,
    scan_state_path: Path | None = None,
) -> GraphBuildStats:
    stats = GraphBuildStats()
    universe = load_universe(universe_path)
    ticker_map = load_sec_ticker_map(client, sec_user_agent)
    resolver = load_name_resolver(client, sec_user_agent)

    scan_state_path = scan_state_path or (client.cache_dir / "scanned_accessions.json")
    scan_state = _load_scan_state(scan_state_path)

    try:
        for entry in universe:
            symbol = entry.symbol
            cik = ticker_map.get(symbol)
            if cik is None:
                stats.symbols_no_cik += 1
                continue
            ref = latest_annual_filing(client, symbol, cik, sec_user_agent)
            if ref is None:
                stats.symbols_no_10k += 1
                continue
            if scan_state.get(symbol) == ref.accession_number:
                stats.symbols_skipped_already_scanned += 1
                continue

            text = fetch_filing_text(client, ref, sec_user_agent)
            candidates = find_candidate_passages(symbol, text)
            stats.filings_scanned += 1
            stats.candidate_passages += len(candidates)
            logger.info(
                "scan %s %s filed %s: %d candidate passage(s)",
                symbol,
                ref.accession_number,
                ref.filed_at,
                len(candidates),
            )

            confirmed = confirmer.confirm_batch(symbol, candidates) if candidates else []
            extracted_at = datetime.now(timezone.utc)
            for cand, edge in zip(candidates, confirmed):
                if edge is None:
                    continue
                stats.confirmed_edges += 1
                resolved = (
                    resolve_counterparty(edge.counterparty_name, resolver) if edge.named else None
                )
                # A "resolution" to the filer itself is a misread, not an edge target.
                if resolved == symbol:
                    resolved = None
                inserted = store.record_frontier_edge(
                    edge_id=str(uuid.uuid4()),
                    edge_type=edge.edge_type,
                    from_symbol=symbol,
                    counterparty_name=edge.counterparty_name,
                    counterparty_canonical=canonical_counterparty(edge.counterparty_name),
                    counterparty_symbol=resolved,
                    named=edge.named,
                    magnitude_pct=edge.magnitude_pct,
                    quoted_passage=cand.passage,
                    accession_number=ref.accession_number,
                    filing_url=ref.url,
                    form=ref.form,
                    filed_at=ref.filed_at,
                    extracted_at=extracted_at,
                    model_id=getattr(confirmer, "model_id", None),
                )
                if not inserted:
                    continue
                stats.edges_inserted += 1
                stats.edges_by_type[edge.edge_type] = stats.edges_by_type.get(edge.edge_type, 0) + 1
                if edge.named:
                    stats.named_edges += 1
                    if resolved:
                        stats.resolved_tickers += 1
                else:
                    stats.anonymous_edges += 1

            scan_state[symbol] = ref.accession_number
            scan_state_path.write_text(json.dumps(scan_state, indent=2), encoding="utf-8")
    except SpendCapExceeded as exc:
        logger.warning("stopping graph build: %s", exc)
        stats.stopped_by_spend_cap = True

    stats.llm_input_tokens = getattr(confirmer, "total_input_tokens", 0)
    stats.llm_output_tokens = getattr(confirmer, "total_output_tokens", 0)
    stats.llm_requests = getattr(confirmer, "requests_made", 0)
    stats.llm_spend_usd = getattr(confirmer, "spend_usd", 0.0)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frontier 3a typed-edge graph builder")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--db", default="data/state.db")
    parser.add_argument("--cache-dir", default="fixtures/frontier_cache")
    parser.add_argument("--spend-cap", type=float, default=5.0)
    parser.add_argument("--offline", action="store_true", help="Cache only; no network")
    parser.add_argument("--stats-out", default="reports/3a-graph-stats.json")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = load_env()
    store = StateStore(args.db)
    client = CachedHTTP(cache_dir=args.cache_dir, offline=args.offline)
    confirmer = HaikuEdgeConfirmer(env.anthropic_api_key, spend_cap_usd=args.spend_cap)

    stats = build_graph(
        store=store,
        client=client,
        confirmer=confirmer,
        sec_user_agent=env.sec_user_agent,
        universe_path=args.universe,
    )
    payload = stats.to_dict()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    out = Path(args.stats_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

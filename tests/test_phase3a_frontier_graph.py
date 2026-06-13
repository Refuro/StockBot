"""Phase 3a — Frontier typed-edge graph builder (offline fixtures only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from stockbot.bots.frontier.build_graph import build_graph
from stockbot.bots.frontier.candidates import (
    CUSTOMER_CONCENTRATION,
    REGULATORY_PRECEDENT,
    SOLE_SUPPLIER,
    CandidatePassage,
    find_candidate_passages,
)
from stockbot.bots.frontier.confirm import ConfirmedEdge, validate_llm_edge
from stockbot.bots.frontier.filings import html_to_text, latest_annual_filing
from stockbot.bots.frontier.resolve import (
    build_name_resolver,
    normalize_company_name,
    resolve_counterparty,
)
from stockbot.core.state.store import StateStore
from stockbot.data.real_loader import (
    SEC_SUBMISSIONS_URL,
    SEC_TICKERS_URL,
    CachedHTTP,
    cache_file_path,
)
from stockbot.research.precision_sample import render_precision_sample

FIXTURES = Path(__file__).parent.parent / "fixtures" / "frontier"
UTC = timezone.utc


# --- candidates --------------------------------------------------------------


def _acme_text() -> str:
    return html_to_text((FIXTURES / "filing_acme_10k.html").read_text(encoding="utf-8"))


def test_html_to_text_strips_scripts_and_tags() -> None:
    text = _acme_text()
    assert "27% of nothing" not in text  # script content removed
    assert "color: black" not in text  # style removed
    assert "Globex" in text
    assert "—" in text  # entities unescaped


def test_candidate_finder_locates_typed_passages_and_skips_below_threshold() -> None:
    text = _acme_text()
    candidates = find_candidate_passages("ACME", text)
    by_hint = {}
    for c in candidates:
        by_hint.setdefault(c.edge_type_hint, []).append(c)

    conc = by_hint.get(CUSTOMER_CONCENTRATION, [])
    assert any("Globex" in c.passage and c.max_pct and c.max_pct >= 27 for c in conc)
    assert any("22%" in c.passage for c in conc)  # anonymous single customer
    # The 12%-only sentence must NOT produce its own concentration candidate.
    assert not any(c.max_pct is not None and c.max_pct < 20 for c in conc)

    assert any("Initech" in c.passage for c in by_hint.get(SOLE_SUPPLIER, []))
    assert any("consent decree" in c.passage.lower() for c in by_hint.get(REGULATORY_PRECEDENT, []))


# --- LLM answer re-validation (the honesty guard) ----------------------------


def _cand(passage: str, hint: str = CUSTOMER_CONCENTRATION) -> CandidatePassage:
    return CandidatePassage(symbol="ACME", edge_type_hint=hint, passage=passage, max_pct=None)


def test_validate_rejects_pct_not_in_passage() -> None:
    cand = _cand("Globex Corporation accounted for approximately 27% of our net sales.")
    raw = {
        "is_edge": True,
        "edge_type": CUSTOMER_CONCENTRATION,
        "counterparty_name": "Globex Corporation",
        "named": True,
        "magnitude_pct": 35.0,  # model hallucinated a different number
    }
    assert validate_llm_edge(raw, cand) is None


def test_validate_rejects_name_not_in_passage() -> None:
    cand = _cand("One customer accounted for approximately 22% of our total revenues.")
    raw = {
        "is_edge": True,
        "edge_type": CUSTOMER_CONCENTRATION,
        "counterparty_name": "Globex Corporation",  # invented name
        "named": True,
        "magnitude_pct": 22.0,
    }
    assert validate_llm_edge(raw, cand) is None


def test_validate_rejects_below_materiality_floor() -> None:
    cand = _cand("Wayne Enterprises accounted for approximately 12% of our net sales.")
    raw = {
        "is_edge": True,
        "edge_type": CUSTOMER_CONCENTRATION,
        "counterparty_name": "Wayne Enterprises",
        "named": True,
        "magnitude_pct": 12.0,
    }
    assert validate_llm_edge(raw, cand) is None


def test_validate_accepts_certified_edge() -> None:
    cand = _cand("Globex Corporation accounted for approximately 27% of our net sales.")
    raw = {
        "is_edge": True,
        "edge_type": CUSTOMER_CONCENTRATION,
        "counterparty_name": "Globex Corporation",
        "named": True,
        "magnitude_pct": 27.0,
    }
    edge = validate_llm_edge(raw, cand)
    assert edge == ConfirmedEdge(
        edge_type=CUSTOMER_CONCENTRATION,
        counterparty_name="Globex Corporation",
        named=True,
        magnitude_pct=27.0,
    )


# --- counterparty resolution (conservative only) ------------------------------


def test_normalize_company_name() -> None:
    assert normalize_company_name("Apple Inc.") == "APPLE"
    assert normalize_company_name("The Walt Disney Company") == "WALT DISNEY"
    assert normalize_company_name("Globex Corporation") == "GLOBEX"


def test_resolver_share_classes_and_ambiguity() -> None:
    raw = {
        "0": {"cik_str": 1, "ticker": "GOOGL", "title": "Alphabet Inc."},
        "1": {"cik_str": 1, "ticker": "GOOG", "title": "Alphabet Inc."},
        "2": {"cik_str": 2, "ticker": "GBX", "title": "Globex Corp"},
        "3": {"cik_str": 3, "ticker": "GBXX", "title": "Globex Inc"},  # same normalized name, different CIK
    }
    resolver = build_name_resolver(raw)
    assert resolve_counterparty("Alphabet Inc.", resolver) == "GOOGL"  # first-listed share class
    assert resolve_counterparty("Globex Corporation", resolver) is None  # ambiguous -> drop
    assert resolve_counterparty("Initech, Inc.", resolver) is None  # unknown stays a name


# --- StateStore edges + liveness ----------------------------------------------


def _record_edge(store: StateStore, **overrides) -> bool:
    base = dict(
        edge_id=overrides.pop("edge_id", "e1"),
        edge_type=CUSTOMER_CONCENTRATION,
        from_symbol="ACME",
        counterparty_name="Globex Corporation",
        counterparty_symbol="GBX",
        named=True,
        magnitude_pct=27.0,
        quoted_passage="Globex Corporation accounted for approximately 27% of our net sales.",
        accession_number="0000000001-26-000001",
        filing_url="https://www.sec.gov/Archives/edgar/data/1/000000000126000001/acme-10k.htm",
        form="10-K",
        filed_at="2026-01-15",
        extracted_at=datetime(2026, 6, 11, tzinfo=UTC),
        model_id="claude-haiku-4-5-20251001",
    )
    base.update(overrides)
    return store.record_frontier_edge(**base)


def test_edge_insert_is_idempotent_and_queryable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    assert _record_edge(store) is True
    assert _record_edge(store, edge_id="e2") is False  # same (filer, accession, type, name)
    edges = store.get_frontier_edges(from_symbol="acme")
    assert len(edges) == 1
    assert edges[0]["counterparty_symbol"] == "GBX"
    assert edges[0]["named"] is True


def test_liveness_query_enforces_18_month_decay_and_named_only(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    _record_edge(store, edge_id="fresh", filed_at="2026-01-15")
    _record_edge(
        store,
        edge_id="stale",
        accession_number="0000000001-24-000001",
        filed_at="2024-06-01",  # > 18 months before as_of
    )
    _record_edge(
        store,
        edge_id="anon",
        accession_number="0000000001-26-000002",
        counterparty_name="one customer",
        counterparty_symbol=None,
        named=False,
        magnitude_pct=22.0,
    )
    as_of = datetime(2026, 6, 11, tzinfo=UTC)
    live = store.get_live_frontier_edges(as_of)
    assert [e["edge_id"] for e in live] == ["fresh"]  # stale decayed, anon excluded
    with_anon = store.get_live_frontier_edges(as_of, named_only=False)
    assert {e["edge_id"] for e in with_anon} == {"fresh", "anon"}


# --- end-to-end pipeline (offline, stub confirmer) -----------------------------


class StubConfirmer:
    """Deterministic confirmer: applies the same code-side validation contract."""

    model_id = "stub-confirmer"
    total_input_tokens = 0
    total_output_tokens = 0
    requests_made = 0
    spend_usd = 0.0

    def confirm_batch(self, filer_symbol, candidates):
        out = []
        for c in candidates:
            if c.edge_type_hint == CUSTOMER_CONCENTRATION and "Globex" in c.passage:
                out.append(
                    validate_llm_edge(
                        {
                            "is_edge": True,
                            "edge_type": CUSTOMER_CONCENTRATION,
                            "counterparty_name": "Globex Corporation",
                            "named": True,
                            "magnitude_pct": 27.0,
                        },
                        c,
                    )
                )
            elif c.edge_type_hint == CUSTOMER_CONCENTRATION and "one customer" in c.passage:
                out.append(
                    validate_llm_edge(
                        {
                            "is_edge": True,
                            "edge_type": CUSTOMER_CONCENTRATION,
                            "counterparty_name": "one customer",
                            "named": False,
                            "magnitude_pct": 22.0,
                        },
                        c,
                    )
                )
            elif c.edge_type_hint == SOLE_SUPPLIER and "Initech" in c.passage:
                out.append(
                    ConfirmedEdge(
                        edge_type=SOLE_SUPPLIER,
                        counterparty_name="Initech, Inc.",
                        named=True,
                        magnitude_pct=None,
                    )
                )
            else:
                out.append(None)
        self.requests_made += 1
        return out


def _seed_offline_cache(cache_dir: Path, accession: str = "0000000001-26-000001") -> None:
    """Seed company_tickers, submissions, and the filing doc into the HTTP cache."""
    tickers_payload = {
        "0": {"cik_str": 777, "ticker": "ACME", "title": "Acme Corp"},
        "1": {"cik_str": 888, "ticker": "GBX", "title": "Globex Corporation"},
    }
    cache_file_path(cache_dir, SEC_TICKERS_URL).write_text(
        json.dumps(tickers_payload), encoding="utf-8"
    )
    submissions = {
        "filings": {
            "recent": {
                "form": ["8-K", "10-K"],
                "accessionNumber": ["0000000001-26-000099", accession],
                "filingDate": ["2026-02-01", "2026-01-15"],
                "primaryDocument": ["ev.htm", "acme-10k.htm"],
            }
        }
    }
    cache_file_path(cache_dir, SEC_SUBMISSIONS_URL.format(cik=777)).write_text(
        json.dumps(submissions), encoding="utf-8"
    )
    filing_url = (
        f"https://www.sec.gov/Archives/edgar/data/777/{accession.replace('-', '')}/acme-10k.htm"
    )
    cache_file_path(cache_dir, filing_url).with_suffix(".txt").write_text(
        (FIXTURES / "filing_acme_10k.html").read_text(encoding="utf-8"), encoding="utf-8"
    )


def _write_universe(path: Path) -> Path:
    path.write_text("symbol,sector\nACME,Industrials\nNOCIK,Tech\n", encoding="utf-8")
    return path


def test_build_graph_offline_end_to_end(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _seed_offline_cache(cache_dir)
    universe = _write_universe(tmp_path / "universe.csv")
    store = StateStore(tmp_path / "state.db")
    client = CachedHTTP(cache_dir=cache_dir, offline=True)
    confirmer = StubConfirmer()

    stats = build_graph(
        store=store,
        client=client,
        confirmer=confirmer,
        sec_user_agent="test test@example.com",
        universe_path=universe,
    )

    assert stats.filings_scanned == 1
    assert stats.symbols_no_cik == 1  # NOCIK has no SEC mapping
    assert stats.candidate_passages >= 3
    assert stats.edges_inserted == 3
    assert stats.edges_by_type[CUSTOMER_CONCENTRATION] == 2
    assert stats.edges_by_type[SOLE_SUPPLIER] == 1
    assert stats.named_edges == 2 and stats.anonymous_edges == 1
    assert stats.resolved_tickers == 1  # Globex -> GBX; Initech unresolved

    edges = store.get_frontier_edges(from_symbol="ACME")
    globex = next(e for e in edges if e["counterparty_name"] == "Globex Corporation")
    assert globex["counterparty_symbol"] == "GBX"
    assert globex["magnitude_pct"] == 27.0
    assert "27%" in globex["quoted_passage"]
    assert globex["accession_number"] == "0000000001-26-000001"
    assert globex["filing_url"].startswith("https://www.sec.gov/Archives/")
    assert globex["filed_at"] == "2026-01-15"
    assert globex["extracted_at"]

    # Re-run: scan state must skip the unchanged filing (zero new LLM calls).
    confirmer2 = StubConfirmer()
    stats2 = build_graph(
        store=store,
        client=client,
        confirmer=confirmer2,
        sec_user_agent="test test@example.com",
        universe_path=universe,
    )
    assert stats2.symbols_skipped_already_scanned == 1
    assert stats2.filings_scanned == 0
    assert confirmer2.requests_made == 0
    assert len(store.get_frontier_edges(from_symbol="ACME")) == 3


def test_latest_annual_filing_picks_most_recent_10k(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K", "10-K/A", "8-K"],
                "accessionNumber": ["a-old", "a-amended", "a-8k"],
                "filingDate": ["2025-03-01", "2025-06-01", "2026-01-01"],
                "primaryDocument": ["old.htm", "amended.htm", "ev.htm"],
            }
        }
    }
    cache_file_path(cache_dir, SEC_SUBMISSIONS_URL.format(cik=777)).write_text(
        json.dumps(submissions), encoding="utf-8"
    )
    client = CachedHTTP(cache_dir=cache_dir, offline=True)
    ref = latest_annual_filing(client, "acme", 777, "test test@example.com")
    assert ref is not None
    assert ref.form == "10-K/A" and ref.accession_number == "a-amended"
    assert ref.url == "https://www.sec.gov/Archives/edgar/data/777/aamended/amended.htm"


# --- precision sample -----------------------------------------------------------


def test_precision_sample_is_seeded_and_complete(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    for i in range(60):
        _record_edge(
            store,
            edge_id=f"e{i:03d}",
            accession_number=f"0000000001-26-{i:06d}",
            counterparty_name=f"Counterparty {i}",
            quoted_passage=f"Counterparty {i} accounted for approximately 27% of our net sales.",
        )
    edges = store.get_frontier_edges(named_only=True)
    report_a = render_precision_sample(edges, seed=3001, sample_size=50)
    report_b = render_precision_sample(edges, seed=3001, sample_size=50)
    # Deterministic apart from the generation timestamp line.
    strip = lambda r: "\n".join(l for l in r.splitlines() if not l.startswith("Generated:"))
    assert strip(report_a) == strip(report_b)
    assert report_a.count("## ") == 50
    assert "sample size 50 of 60 named edges" in report_a
    assert "Verdict:" in report_a
    assert "https://www.sec.gov/Archives/" in report_a

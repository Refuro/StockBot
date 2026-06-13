"""Shot 7 (Agent B) — FrontierEdgeValidator + dedupe + combined re-filter.

All deterministic/offline: Haiku is a StubLLMClient returning canned JSON by
substring match; no network, no real spend.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stockbot.bots.frontier.candidates import (
    CUSTOMER_CONCENTRATION,
    REGULATORY_PRECEDENT,
    SOLE_SUPPLIER,
)
from stockbot.bots.frontier.dedupe import dedupe_edges
from stockbot.bots.frontier.validator import (
    NUM_CHECKS,
    VALIDATOR_SYSTEM,
    EdgeValidation,
    FrontierEdgeValidator,
    build_edge_prompt,
)
from stockbot.core.llm import LLMResponse, LLMSpendCapExceeded, ModelTier, StubLLMClient, build_router
from stockbot.core.state.store import StateStore
from stockbot.research.refilter_graph import combined_refilter

UTC = timezone.utc
HAIKU = "claude-haiku-4-5-20251001"


def _edge(edge_id, edge_type, src, counterparty, magnitude, quote, **kw):
    return {
        "edge_id": edge_id,
        "edge_type": edge_type,
        "from_symbol": src,
        "counterparty_name": counterparty,
        "counterparty_canonical": kw.get("canonical", counterparty.upper()),
        "counterparty_symbol": kw.get("symbol"),
        "named": kw.get("named", True),
        "magnitude_pct": magnitude,
        "quoted_passage": quote,
        "accession_number": kw.get("accession", "0000000000-26-000001"),
        "filing_url": "https://sec.gov/x",
        "form": "10-K",
        "filed_at": "2026-03-01",
        "extracted_at": "2026-03-02T00:00:00+00:00",
        "model_id": HAIKU,
    }


def _router(responses, *, cap=2.0):
    stub = StubLLMClient(HAIKU, responses=responses, default="{}")
    return build_router(cap_usd=cap, label="test-validator", clients={ModelTier.TRIAGE: stub})


# ---------------------------------------------------------------------------
# Validator — frozen rubric + prompt
# ---------------------------------------------------------------------------


def test_rubric_frozen_has_all_five_checks():
    for n in range(1, NUM_CHECKS + 1):
        assert f"CHECK {n}" in VALIDATOR_SYSTEM
    assert '"valid"' in VALIDATOR_SYSTEM and '"failed_check"' in VALIDATOR_SYSTEM


def test_prompt_includes_edge_fields_and_truncates_quote():
    edge = _edge("e1", SOLE_SUPPLIER, "AAA", "Acme Corp", None, "x" * 5000)
    prompt = build_edge_prompt(edge, max_quote_chars=100)
    assert "Acme Corp" in prompt
    assert "sole_supplier" in prompt
    assert "AAA" in prompt
    assert "[…]" in prompt  # truncated
    assert len(prompt) < 600


# ---------------------------------------------------------------------------
# Validator — each of the 5 checks fires (stubbed Haiku)
# ---------------------------------------------------------------------------


def test_each_check_fires_and_valid_passes():
    edges = {
        1: _edge("c1", REGULATORY_PRECEDENT, "S1", "HypoFDA", None, "may include a consent decree"),
        2: _edge("c2", SOLE_SUPPLIER, "S2", "RevCust", None, "we serve as sole supplier to RevCust"),
        3: _edge("c3", SOLE_SUPPLIER, "S3", "ProductX", None, "ProductX is our only product"),
        4: _edge("c4", CUSTOMER_CONCENTRATION, "S4", "StaleCo", 40.0, "40% in 2024 and 5% in 2025"),
        5: _edge("c5", CUSTOMER_CONCENTRATION, "S5", "GoneCo", 30.0, "GoneCo did not renew"),
    }
    good = _edge("ok", SOLE_SUPPLIER, "S6", "GoodCo", None, "GoodCo is our sole supplier of widgets")
    responses = [
        ("HypoFDA", '{"valid": false, "failed_check": 1, "reason": "hedged"}'),
        ("RevCust", '{"valid": false, "failed_check": 2, "reason": "reversed"}'),
        ("ProductX", '{"valid": false, "failed_check": 3, "reason": "product not entity"}'),
        ("StaleCo", '{"valid": false, "failed_check": 4, "reason": "prior year"}'),
        ("GoneCo", '{"valid": false, "failed_check": 5, "reason": "terminated"}'),
        ("GoodCo", '{"valid": true, "failed_check": null, "reason": "asserted"}'),
    ]
    validator = FrontierEdgeValidator(_router(responses))
    for check_num, edge in edges.items():
        v = validator.validate(edge)
        assert v.valid is False
        assert v.failed_check == check_num, f"check {check_num} expected"
    ok = validator.validate(good)
    assert ok.valid is True
    assert ok.failed_check is None


# ---------------------------------------------------------------------------
# Validator — fail-closed behaviour
# ---------------------------------------------------------------------------


def test_unparseable_response_fails_closed():
    validator = FrontierEdgeValidator(_router([("BadCo", "this is not json")]))
    v = validator.validate(_edge("u1", SOLE_SUPPLIER, "S", "BadCo", None, "q"))
    assert v.valid is False
    assert v.failed_check is None
    assert "unparseable" in v.flags and "fail_closed" in v.flags


def test_missing_valid_key_fails_closed():
    validator = FrontierEdgeValidator(_router([("NoKey", '{"reason": "oops"}')]))
    v = validator.validate(_edge("u2", SOLE_SUPPLIER, "S", "NoKey", None, "q"))
    assert v.valid is False
    assert "unparseable" in v.flags


def test_invalid_without_check_number_still_drops_and_flags():
    validator = FrontierEdgeValidator(_router([("Weird", '{"valid": false, "reason": "no num"}')]))
    v = validator.validate(_edge("u3", SOLE_SUPPLIER, "S", "Weird", None, "q"))
    assert v.valid is False
    assert v.failed_check is None
    assert "missing_failed_check" in v.flags


def test_call_exception_fails_closed():
    class _Raising:
        model_id = HAIKU

        def complete(self, prompt, *, system=None, max_tokens=1024):
            raise RuntimeError("api down")

    router = build_router(cap_usd=2.0, label="t", clients={ModelTier.TRIAGE: _Raising()})
    v = FrontierEdgeValidator(router).validate(_edge("x", SOLE_SUPPLIER, "S", "AnyCo", None, "q"))
    assert v.valid is False
    assert "validation_error" in v.flags


def test_spend_cap_exceeded_propagates():
    validator = FrontierEdgeValidator(_router([("Z", '{"valid": true}')], cap=0.0))
    with pytest.raises(LLMSpendCapExceeded):
        validator.validate(_edge("z", SOLE_SUPPLIER, "S", "Z", None, "q"))


def test_cache_prevents_double_spend():
    router = _router([("CacheCo", '{"valid": true, "failed_check": null, "reason": "ok"}')])
    validator = FrontierEdgeValidator(router)
    edge = _edge("cache1", SOLE_SUPPLIER, "S", "CacheCo", None, "q")
    first = validator.validate(edge)
    second = validator.validate(edge)
    assert first is second
    assert router.tracker.calls == 1


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_dedupe_parent_subsidiary_evtc():
    quote_pop = "approximately 29% of our revenue was generated from our relationship with Popular."
    quote_banco = (
        "approximately 29% of our revenue was attributable to Banco Popular, a wholly-owned "
        "subsidiary of Popular."
    )
    edges = [
        _edge("evtc-pop", CUSTOMER_CONCENTRATION, "EVTC", "Popular", 29.0, quote_pop,
              canonical="POPULAR", symbol="BPOP"),
        _edge("evtc-banco", CUSTOMER_CONCENTRATION, "EVTC", "Banco Popular", 29.0, quote_banco,
              canonical="BANCO POPULAR"),
    ]
    survivors, merges = dedupe_edges(edges)
    assert len(survivors) == 1
    assert survivors[0]["edge_id"] == "evtc-pop"  # resolved ticker wins
    assert len(merges) == 1
    assert merges[0].rule == "d4_parent_subsidiary"


def test_dedupe_dual_class_gliba_glibk():
    quote = "On August 8, 2025, GCI entered into a settlement and a three-year consent decree."
    edges = [
        _edge("gliba", REGULATORY_PRECEDENT, "GLIBA", "FCC", None, quote,
              canonical="FCC", accession="0001104659-26-013442"),
        _edge("glibk", REGULATORY_PRECEDENT, "GLIBK", "FCC", None, quote,
              canonical="FCC", accession="0001104659-26-013442"),
    ]
    survivors, merges = dedupe_edges(edges)
    assert len(survivors) == 1
    assert len(merges) == 1
    assert merges[0].rule == "d3_dual_class"


def test_dedupe_identical_quote_same_source():
    q = "Sales to The Home Depot accounted for approximately 37% of our net sales in fiscal 2025."
    edges = [
        _edge("cent-a", CUSTOMER_CONCENTRATION, "CENT", "The Home Depot", 37.0, q,
              canonical="HOME DEPOT", accession="A-1"),
        _edge("cent-b", CUSTOMER_CONCENTRATION, "CENT", "The Home Depot", 37.0, q,
              canonical="HOME DEPOT", accession="A-2"),
    ]
    survivors, merges = dedupe_edges(edges)
    assert len(survivors) == 1
    # D1 (same relationship) takes priority over D2 (identical quote).
    assert merges[0].rule == "d1_same_relationship"


def test_dedupe_keeps_distinct_edges():
    edges = [
        _edge("a", CUSTOMER_CONCENTRATION, "XYZ", "Walmart", 25.0,
              "Walmart accounted for 25% of our net sales.", canonical="WALMART", symbol="WMT"),
        _edge("b", CUSTOMER_CONCENTRATION, "XYZ", "Costco", 11.0,
              "Costco accounted for 11% of our net sales.", canonical="COSTCO", symbol="COST"),
    ]
    survivors, merges = dedupe_edges(edges)
    assert len(survivors) == 2
    assert merges == []


def test_dedupe_containment_without_corroboration_does_not_merge():
    # "Shell" ⊂ "Shell Trading US Company" by tokens, but different magnitudes
    # and no subsidiary wording → conservative: NOT merged.
    edges = [
        _edge("s1", CUSTOMER_CONCENTRATION, "WTI", "Shell", 17.0,
              "Shell accounted for 17% of revenues.", canonical="SHELL", symbol="SHEL"),
        _edge("s2", CUSTOMER_CONCENTRATION, "WTI", "Shell Trading US Company", 35.0,
              "Shell Trading US Company accounted for 35% of revenues.",
              canonical="SHELL TRADING US"),
    ]
    survivors, merges = dedupe_edges(edges)
    assert len(survivors) == 2
    assert merges == []


# ---------------------------------------------------------------------------
# Combined re-filter integration (regex -> dedupe -> stub validator)
# ---------------------------------------------------------------------------


def _record(store, edge):
    store.record_frontier_edge(
        edge_id=edge["edge_id"],
        edge_type=edge["edge_type"],
        from_symbol=edge["from_symbol"],
        counterparty_name=edge["counterparty_name"],
        counterparty_canonical=edge["counterparty_canonical"],
        counterparty_symbol=edge["counterparty_symbol"],
        named=edge["named"],
        magnitude_pct=edge["magnitude_pct"],
        quoted_passage=edge["quoted_passage"],
        accession_number=edge["accession_number"],
        filing_url=edge["filing_url"],
        form=edge["form"],
        filed_at=edge["filed_at"],
        extracted_at=datetime.now(UTC),
        model_id=edge["model_id"],
    )


def test_combined_refilter_three_stages(tmp_path):
    store = StateStore(tmp_path / "state.db")
    hd_quote = "Sales to The Home Depot accounted for approximately 37% of our net sales in 2025."
    _record(store, _edge("hd-a", CUSTOMER_CONCENTRATION, "CENT", "The Home Depot", 37.0, hd_quote,
                         canonical="HOME DEPOT", symbol="HD", accession="ACC-1"))
    _record(store, _edge("hd-b", CUSTOMER_CONCENTRATION, "CENT", "The Home Depot", 37.0, hd_quote,
                         canonical="HOME DEPOT", symbol="HD", accession="ACC-2"))
    # Regex-killable: geography counterparty.
    _record(store, _edge("geo", CUSTOMER_CONCENTRATION, "MVST", "Italy", 22.0,
                         "Italy 92,941 22 % of our net sales by region.", canonical="ITALY",
                         accession="ACC-3"))
    # Survives regex, validator drops it (semantic).
    _record(store, _edge("acme", SOLE_SUPPLIER, "AAA", "Acme Labs", None,
                         "Acme Labs is our sole supplier of widgets.", canonical="ACME LABS",
                         accession="ACC-4"))
    # Survives regex AND validator.
    _record(store, _edge("beta", SOLE_SUPPLIER, "BBB", "Beta Corp", None,
                         "Beta Corp is our sole supplier of reagents.", canonical="BETA CORP",
                         accession="ACC-5"))

    responses = [
        ("The Home Depot", '{"valid": true, "failed_check": null, "reason": "ok"}'),
        ("Acme Labs", '{"valid": false, "failed_check": 3, "reason": "product"}'),
        ("Beta Corp", '{"valid": true, "failed_check": null, "reason": "ok"}'),
    ]
    validator = FrontierEdgeValidator(_router(responses))
    log_path = tmp_path / "validator-log.jsonl"
    stats = combined_refilter(store, validator=validator, validator_log_path=log_path)

    assert stats["before"]["total"] == 5
    assert stats["after_regex"]["total"] == 4  # Italy killed
    assert stats["after_dedupe"]["total"] == 3  # Home Depot dup collapsed
    assert stats["after_final"]["total"] == 2  # Acme dropped by validator
    assert stats["dedupe_merge_count"] == 1
    assert stats["validator"]["ran"] is True
    assert stats["validator"]["kills_by_check"] == {"check_3": 1}
    assert sum(stats["regex_kills_by_guard"].values()) == 1

    remaining = {e["edge_id"] for e in store.get_frontier_edges()}
    assert "beta" in remaining
    assert "acme" not in remaining and "geo" not in remaining
    assert log_path.exists()
    assert len(log_path.read_text(encoding="utf-8").strip().splitlines()) == 3


def test_combined_refilter_without_llm(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _record(store, _edge("beta", SOLE_SUPPLIER, "BBB", "Beta Corp", None,
                         "Beta Corp is our sole supplier of reagents.", canonical="BETA CORP"))
    stats = combined_refilter(store, validator=None)
    assert stats["validator"]["ran"] is False
    assert stats["after_final"]["total"] == 1

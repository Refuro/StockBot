"""Ticket 3a-F — extraction guards, tested against the graded 3b failures.

Every failing passage below is verbatim from `reports/3b-precision-sample.md`
(the edges the reviewer graded ❌ in `reports/3b-grading.md`), and every
passing passage is one the reviewer graded ✅ — the guards must reject the
former and keep the latter.
"""

from __future__ import annotations

from stockbot.bots.frontier.candidates import (
    CUSTOMER_CONCENTRATION,
    REGULATORY_PRECEDENT,
    CandidatePassage,
    find_candidate_passages,
)
from stockbot.bots.frontier.confirm import validate_llm_edge
from stockbot.bots.frontier.guards import (
    aggregate_group_pct,
    canonical_counterparty,
    is_anonymous_placeholder,
    operative_decree,
    revenue_basis_certified,
    supplier_direction,
)

# --- graded passages (verbatim fixtures) -------------------------------------

# ❌ #2 AMRX — aggregate of four customers typed as single-customer concentration
AMRX = (
    "Our four largest customers, Cencora, Inc., McKesson Drug Co., Cardinal Health, Inc. "
    "and CVS Health Corporation, accounted for approximately 71%, 70% and 70% of total net "
    "sales of products for the years ended December 31, 2025, 2024 and 2023, respectively."
)

# ❌ #8 EXTR — Jenne figure is accounts receivable, not revenue
EXTR = (
    "18 % 22 % 15 % Westcon Group, Inc. 18 % 16 % 20 % TD Synnex Corporation 18 % 21 % 18 % "
    "The following table sets forth major customers accounting for 10% or more of the "
    "Company’s net accounts receivable, as of June 30, 2025 and June 30, 2024: "
    "June 30, 2025 June 30, 2024 Jenne, Inc. 22 % 64 % Ericsson Inc."
)

# ❌ #13 HRMY — 45% is accounts receivable (revenue figure is 36%)
HRMY_AR = (
    "Historically, the Company has not experienced credit losses on its accounts receivable. "
    "The Company monitors its exposure within accounts receivable and would record a reserve "
    "against uncollectible accounts receivable if necessary. As of December 31, 2025, three "
    "customers accounted for 100 % of gross accounts receivable, Accredo Health Group, Inc. "
    "(“Accredo”), which accounted for 45 % of gross accounts receivable; Caremark LLC "
    "(“CVS Caremark”), which accounted for 36 % of gross accounts receivable; and PANTHERx "
    "Specialty Pharmacy LLC (“PANTHERx”), which accounted for 19 % of gross accounts receivable."
)

# ❌ #14 KLIC — table is "% of total accounts receivable"
KLIC = (
    "NOTES TO CONSOLIDATED FINANCIAL STATEMENTS - (Continued) The following table reflects "
    "significant customer concentrations as a percentage of total accounts receivable as of "
    "October 4, 2025 and September 28, 2024: As of October 4, 2025 September 28, 2024 "
    "Tianshui Huatian Technology Co., Ltd 30.3 % 17.2 % Haoseng Industrial Co Ltd 13.0 % * "
    "Forehope Group * 15.7 % * Represents less than 10% of total accounts receivable NOTE 17."
)

# ❌ #17 MXL — anonymous placeholder + accounts-receivable basis
MXL = (
    "117 Table of Contents MAXLINEAR, INC. NOTES TO CONSOLIDATED FINANCIAL STATEMENTS The "
    "following table presents balances that are 10% or greater of accounts receivable, based "
    "on the Company’s billings to its customers. December 31, 2025 2024 Percentage of gross "
    "accounts receivable Customer C 26 % 17 % Customer D 14 % * Customer E 10 % * Customer F "
    "10 % * Customer G * 16 % * Represents less than 10% of the gross accounts receivable."
)

# ❌ #20 PLUS/Cisco — supplier direction ("products from Cisco Systems")
PLUS_CSCO = (
    "Our risk on our accounts receivable is reduced by having a broad customer base in a "
    "diverse range of industries and through the ongoing evaluation of collectability of our "
    "portfolio. A substantial portion of our sales are products from Cisco Systems, which "
    "represented approximately 29 %, 32 %, and 44 %, of our net sales for the years ended "
    "March 31, 2026, 2025, and 2024, respectively."
)

# ❌ #21 POWI — accounts-receivable table; quote even says no customer >10% of revenue
POWI = (
    "No other customers accounted for 10% or more of the Company’s net revenue in the periods "
    "presented. Concentration of Credit Risk Financial instruments that potentially subject "
    "the Company to concentrations of credit risk consist principally of cash investments and "
    "trade receivables. As of December 31, 2025 and 2024, 89 % and 87 % of accounts receivable "
    "were concentrated with the Company’s top ten customers, respectively. The following "
    "customers represented 10% or more of accounts receivable: December 31, December 31, "
    "Customer 2025 2024 Avnet 32 % 32 % Salcomp Group 18 % 13 %"
)

# ❌ #5 AZZ — dead decree (obligations satisfied 2018)
AZZ = (
    "ARC ceased using the property in 2005 after which point ARC remained in the Lease to "
    "complete its obligations on the property pursuant to a consent decree entered into "
    "between the U.S. Environmental Protection Agency (“EPA”) and ARC in 1992. ARC satisfied "
    "its obligations under the consent decree in 2018 (other than ongoing well water "
    "monitoring and testing) and terminated the Lease in 2019."
)

# ❌ #6 BTU — filer not a party; EPA published a *proposed* decree
BTU = (
    "States are required to revise plans every 10 years, but these statutory deadlines have "
    "not been met. On March 29, 2024, the EPA published a proposed consent decree under which "
    "deadlines (for the second 10-year regional haze implementation period) would be "
    "established for the EPA to take final action to approve, disapprove or conditionally "
    "approve, in whole or in part, state regional haze implementation plans for 34 states."
)

# ❌ #10 GBX — prospective: "discussing remedial action consent decree terms"
GBX = (
    "Some allocation participants, including us, are discussing remedial action consent "
    "decree terms with the EPA and the U.S. Department of Justice. Responsibility for funding "
    "and implementing the EPA's selected cleanup remedy will be determined at an unspecified "
    "later date as part of the allocation process."
)

# ✅ #1 AMN — single customer, revenue basis, >=20%
AMN = (
    "Kaiser Foundation Hospitals (and its affiliates), to whom we provide clinical managed "
    "services, comprised approximately 22% of our consolidated revenue and 32% of our nurse "
    "and allied solutions segment revenue for the fiscal year ended December 31, 2025. No "
    "other client healthcare system or single client facility comprised more than 5% of our "
    "consolidated revenue for the fiscal year ended December 31, 2025."
)

# ✅ #11 HRMY/Caremark — 38% of gross product revenue (despite plural lead-in clause)
HRMY_REV = (
    "Customers For the year ended December 31, 2025, three customers accounted for 100% of "
    "gross product revenue; Caremark LLC accounted for 38% of gross product revenue; Accredo "
    "Health Group, Inc. accounted for 36% of gross product revenue; and PANTHERx Specialty "
    "Pharmacy LLC accounted for 26% of gross product revenue."
)

# ✅ #16 MRTN/Walmart — 20% of revenue (the 24% trade-receivables figure must not poison it)
MRTN = (
    "Our largest customer, Walmart, accounted for 20 % of our revenue excluding fuel "
    "surcharges in 2025 and 24 % of our trade receivables as of December 31, 2025, 20 % of "
    "our revenue in 2024 and 21 % of our trade receivables as of December 31, 2024, and 19 % "
    "of our revenue in 2023."
)

# ✅ #18 PCRX — "three wholesalers ... 31%, 26% and 22% ... respectively" distributes
PCRX = (
    "Significant Customers We had three wholesalers each comprising 10 percent or more of our "
    "total revenue for the year ended December 31, 2025: McKesson Drug Company, Cardinal "
    "Health, Inc. and AmerisourceBergen Health Corporation, which accounted for 31%, 26% and "
    "22% of our total revenues, respectively."
)

# ✅ #23 TALO — individually attributed revenue percentages
TALO = (
    "For the year ended December 31, 2025, 35%, 23% and 12% of our oil, natural gas and NGL "
    "revenues were attributable to Shell Trading (US) Company, Exxon Mobil Corporation and "
    "Chevron Corporation, respectively, which are the customers that individually represented "
    "10% or more of our oil, natural gas and NGL revenues."
)

# ✅ #3 ARCB / #7 CTS / #22 SUPN / #24 TEX — operative decrees
ARCB = (
    "In 2023, ABF Freight entered into a Consent Decree with the Environmental Protection "
    "Agency (the “EPA”) to resolve alleged compliance issues under the federal Clean Water "
    "Act, agreeing to certain compliance tasks."
)
CTS = (
    "CTS CORPORATION 56 Table of Contents The Company operates under and in accordance with "
    "a federal consent decree, dated March 7, 2017, with the EPA for the Asheville Site."
)
SUPN = (
    "In March 2019, MDD US Operations, LLC and its subsidiary, Solstice Neurosciences, LLC, "
    "which are subsidiaries of the Company, entered into a Corporate Integrity Agreement "
    "(CIA) with the Office of Inspector General of the U.S. Department of Health and Human "
    "Services."
)
TEX = (
    "We must comply with an injunction and related obligations imposed by the SEC. We and "
    "our directors, officers and employees are required to comply at all times with the "
    "terms of a 2009 settlement with the SEC."
)


# --- guard 1: revenue basis ----------------------------------------------------


class TestRevenueBasisGuard:
    def test_rejects_accounts_receivable_contexts(self) -> None:
        assert revenue_basis_certified(EXTR, 22.0) is False
        assert revenue_basis_certified(HRMY_AR, 45.0) is False
        assert revenue_basis_certified(KLIC, 30.3) is False
        assert revenue_basis_certified(MXL, 26.0) is False
        assert revenue_basis_certified(POWI, 32.0) is False

    def test_keeps_revenue_contexts(self) -> None:
        assert revenue_basis_certified(AMN, 22.0) is True
        assert revenue_basis_certified(HRMY_REV, 38.0) is True
        assert revenue_basis_certified(PCRX, 31.0) is True
        assert revenue_basis_certified(TALO, 35.0) is True

    def test_mixed_sentence_uses_nearest_basis_per_occurrence(self) -> None:
        # MRTN: 20% -> revenue (keep); 24% -> trade receivables (reject)
        assert revenue_basis_certified(MRTN, 20.0) is True
        assert revenue_basis_certified(MRTN, 24.0) is False


# --- guard 2: aggregate group ---------------------------------------------------


class TestAggregateGroupGuard:
    def test_rejects_group_aggregate(self) -> None:
        assert aggregate_group_pct(AMRX, 71.0) is True

    def test_keeps_individually_attributed_lists(self) -> None:
        # "three wholesalers ... 31%, 26% and 22% ... respectively" distributes
        assert aggregate_group_pct(PCRX, 31.0) is False
        # plural lead-in clause is separated by ';' from the individual figure
        assert aggregate_group_pct(HRMY_REV, 38.0) is False
        assert aggregate_group_pct(AMN, 22.0) is False


# --- guard 3: operative decree ---------------------------------------------------


class TestOperativeDecreeGuard:
    def test_rejects_dead_proposed_and_third_party_decrees(self) -> None:
        assert operative_decree(AZZ) is False  # satisfied/terminated
        assert operative_decree(BTU) is False  # proposed, filer not a party
        assert operative_decree(GBX) is False  # discussing terms

    def test_keeps_operative_decrees(self) -> None:
        assert operative_decree(ARCB) is True
        assert operative_decree(CTS) is True
        assert operative_decree(SUPN) is True
        assert operative_decree(TEX) is True


# --- guard 4: supplier direction --------------------------------------------------


class TestSupplierDirectionGuard:
    def test_rejects_products_from_counterparty(self) -> None:
        assert supplier_direction(PLUS_CSCO, "Cisco Systems") is True

    def test_keeps_sales_to_counterparty(self) -> None:
        verizon = (
            "Sales to Verizon Communications Inc. represented 24%, 17%, and 19% of our net "
            "sales for the years ended March 31, 2026, 2025, and 2024, respectively."
        )
        assert supplier_direction(verizon, "Verizon Communications Inc.") is False
        assert supplier_direction(MRTN, "Walmart") is False


# --- guard 5: anonymous placeholder ------------------------------------------------


class TestAnonymousPlaceholderGuard:
    def test_placeholders_are_not_named(self) -> None:
        for name in ("Customer C", "customer one", "Client B", "Customer 3"):
            assert is_anonymous_placeholder(name) is True

    def test_real_names_are_named(self) -> None:
        for name in ("Caremark LLC", "Customer Engineering Corp", "C Spire"):
            assert is_anonymous_placeholder(name) is False


# --- entity-normalized dedupe --------------------------------------------------------


class TestCanonicalCounterparty:
    def test_regulator_aliases_collapse(self) -> None:
        assert (
            canonical_counterparty("EPA")
            == canonical_counterparty("Environmental Protection Agency")
            == canonical_counterparty("U.S. Environmental Protection Agency")
        )
        assert canonical_counterparty("SEC") == canonical_counterparty(
            "Securities and Exchange Commission"
        )

    def test_caremark_aliases_collapse(self) -> None:
        assert canonical_counterparty("Caremark LLC") == canonical_counterparty("CVS Caremark")

    def test_distinct_entities_stay_distinct(self) -> None:
        assert canonical_counterparty("McKesson Drug Company") != canonical_counterparty(
            "Cardinal Health, Inc."
        )


# --- canonical dedupe at the store level ---------------------------------------------


def test_store_dedupes_on_canonical_identity(tmp_path) -> None:
    from datetime import datetime, timezone

    from stockbot.core.state.store import StateStore

    store = StateStore(tmp_path / "state.db")

    def record(edge_id: str, name: str) -> bool:
        return store.record_frontier_edge(
            edge_id=edge_id,
            edge_type=REGULATORY_PRECEDENT,
            from_symbol="ARCB",
            counterparty_name=name,
            counterparty_canonical=canonical_counterparty(name),
            counterparty_symbol=None,
            named=True,
            magnitude_pct=None,
            quoted_passage=ARCB,
            accession_number="0001104659-26-019699",
            filing_url="https://www.sec.gov/Archives/x",
            form="10-K",
            filed_at="2026-02-25",
            extracted_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
            model_id="test",
        )

    assert record("e1", "Environmental Protection Agency") is True
    assert record("e2", "EPA") is False  # same canonical identity -> dedupe
    assert len(store.get_frontier_edges(from_symbol="ARCB")) == 1


# --- guards integrated into validate_llm_edge -------------------------------------------


def _cc_raw(name: str, pct: float, named: bool = True) -> dict:
    return {
        "is_edge": True,
        "edge_type": CUSTOMER_CONCENTRATION,
        "counterparty_name": name,
        "named": named,
        "magnitude_pct": pct,
    }


def _cand(passage: str, hint: str = CUSTOMER_CONCENTRATION) -> CandidatePassage:
    return CandidatePassage(symbol="TEST", edge_type_hint=hint, passage=passage, max_pct=None)


class TestValidationIntegration:
    def test_graded_failures_are_now_rejected(self) -> None:
        assert validate_llm_edge(_cc_raw("Cencora, Inc.", 71.0), _cand(AMRX)) is None
        assert validate_llm_edge(_cc_raw("Jenne, Inc.", 22.0), _cand(EXTR)) is None
        assert validate_llm_edge(_cc_raw("Accredo Health Group, Inc.", 45.0), _cand(HRMY_AR)) is None
        assert (
            validate_llm_edge(_cc_raw("Tianshui Huatian Technology Co., Ltd", 30.3), _cand(KLIC))
            is None
        )
        assert validate_llm_edge(_cc_raw("Avnet", 32.0), _cand(POWI)) is None
        assert validate_llm_edge(_cc_raw("Cisco Systems", 29.0), _cand(PLUS_CSCO)) is None
        for passage in (AZZ, BTU, GBX):
            raw = {
                "is_edge": True,
                "edge_type": REGULATORY_PRECEDENT,
                "counterparty_name": "EPA",
                "named": True,
                "magnitude_pct": None,
            }
            assert validate_llm_edge(raw, _cand(passage, REGULATORY_PRECEDENT)) is None

    def test_placeholder_is_demoted_to_anonymous_not_rejected_outright(self) -> None:
        passage = (
            "The following customer accounted for 10% or more of total revenues: "
            "Customer C 26 % of total revenues."
        )
        edge = validate_llm_edge(_cc_raw("Customer C", 26.0), _cand(passage))
        assert edge is not None and edge.named is False

    def test_graded_correct_edges_still_pass(self) -> None:
        assert validate_llm_edge(
            _cc_raw("Kaiser Foundation Hospitals (and its affiliates)", 22.0), _cand(AMN)
        ) is not None
        assert validate_llm_edge(_cc_raw("Caremark LLC", 38.0), _cand(HRMY_REV)) is not None
        assert validate_llm_edge(_cc_raw("McKesson Drug Company", 31.0), _cand(PCRX)) is not None
        assert validate_llm_edge(_cc_raw("Shell Trading (US) Company", 35.0), _cand(TALO)) is not None
        assert validate_llm_edge(_cc_raw("Walmart", 20.0), _cand(MRTN)) is not None
        for passage, counterparty in (
            (ARCB, "Environmental Protection Agency"),
            (CTS, "EPA"),
            (TEX, "SEC"),
        ):
            raw = {
                "is_edge": True,
                "edge_type": REGULATORY_PRECEDENT,
                "counterparty_name": counterparty,
                "named": True,
                "magnitude_pct": None,
            }
            assert validate_llm_edge(raw, _cand(passage, REGULATORY_PRECEDENT)) is not None


# --- candidate-stage pre-filters (spend savers) ---------------------------------------


class TestCandidateStagePrefilters:
    def test_ar_only_text_produces_no_concentration_candidates(self) -> None:
        candidates = find_candidate_passages("KLIC", KLIC)
        assert not [c for c in candidates if c.edge_type_hint == CUSTOMER_CONCENTRATION]

    def test_proposed_decree_text_produces_no_regulatory_candidates(self) -> None:
        candidates = find_candidate_passages("BTU", BTU)
        assert not [c for c in candidates if c.edge_type_hint == REGULATORY_PRECEDENT]

    def test_operative_decree_text_still_produces_candidates(self) -> None:
        candidates = find_candidate_passages("CTS", CTS)
        assert [c for c in candidates if c.edge_type_hint == REGULATORY_PRECEDENT]

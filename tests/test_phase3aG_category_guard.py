"""Ticket 3a-G — counterparty-category guard (from the 3b re-grade).

The single miss in `reports/3b-grading-2.md` (edge #1): AHCO's 26% attaches to
"government healthcare programs, including Medicare and Medicaid" — a payor
*category*, not a tradeable counterparty. Categories are demoted to the
anonymous pool exactly like "Customer C" placeholders.
"""

from __future__ import annotations

from stockbot.bots.frontier.candidates import CandidatePassage
from stockbot.bots.frontier.confirm import validate_llm_edge
from stockbot.bots.frontier.guards import is_counterparty_category

# The graded miss, verbatim from reports/3b-precision-sample-2.md edge #1.
AHCO_PASSAGE = (
    "This results in a customer concentration relating to government healthcare "
    "reimbursement programs. During the years ended December 31, 2025, 2024 and "
    "2023, the Company derived approximately 26 %, 26 % and 27 % of its net "
    "revenue from government healthcare programs, including Medicare and "
    "Medicaid, respectively. Each of the Company's reportable segments sold to "
    "government healthcare reimbursement programs during the years ended "
    "December 31, 2025, 2024 and 2023."
)


class TestCategoryGuard:
    def test_rejects_the_graded_ahco_payor_category(self) -> None:
        assert is_counterparty_category(
            "government healthcare programs, including Medicare and Medicaid"
        )

    def test_rejects_plural_head_nouns(self) -> None:
        assert is_counterparty_category("government healthcare programs")
        assert is_counterparty_category("commercial insurers")
        assert is_counterparty_category("third-party payors")
        assert is_counterparty_category("managed care organizations")
        assert is_counterparty_category("pharmacy benefit intermediaries")

    def test_rejects_government_program_collectives(self) -> None:
        assert is_counterparty_category("Medicare and Medicaid")
        assert is_counterparty_category("Medicare, Medicaid and TRICARE")

    def test_keeps_single_named_entities(self) -> None:
        # Every named counterparty the reviewer graded CORRECT must survive.
        for name in (
            "Kaiser Foundation Hospitals",
            "Kaiser Foundation Health Plan",  # singular "Plan" is not a category
            "CVS Caremark",
            "SK hynix",
            "McKesson Corporation",
            "Walmart Inc.",
            "Shell Trading Company",
            "Environmental Protection Agency",  # singular "Agency"
            "Verizon",
            "General Motors",
        ):
            assert not is_counterparty_category(name), name

    def test_exemplifying_tail_does_not_rescue_a_category(self) -> None:
        assert is_counterparty_category(
            "government payors, such as Medicare"
        )


class TestValidationIntegration:
    def test_ahco_category_edge_is_demoted_to_anonymous(self) -> None:
        candidate = CandidatePassage(
            symbol="AHCO",
            edge_type_hint="customer_concentration",
            passage=AHCO_PASSAGE,
            max_pct=27.0,
        )
        raw = {
            "index": 0,
            "is_edge": True,
            "edge_type": "customer_concentration",
            "counterparty_name": "government healthcare programs, including Medicare and Medicaid",
            "named": True,
            "magnitude_pct": 26.0,
        }
        edge = validate_llm_edge(raw, candidate)
        assert edge is not None  # the dependency is real and stats-worthy...
        assert edge.named is False  # ...but it is NOT a named graph edge

    def test_named_single_customer_untouched(self) -> None:
        passage = (
            "Kaiser Foundation Hospitals accounted for approximately 22% of our "
            "consolidated revenue for the year ended December 31, 2025."
        )
        candidate = CandidatePassage(
            symbol="AMN",
            edge_type_hint="customer_concentration",
            passage=passage,
            max_pct=22.0,
        )
        raw = {
            "index": 0,
            "is_edge": True,
            "edge_type": "customer_concentration",
            "counterparty_name": "Kaiser Foundation Hospitals",
            "named": True,
            "magnitude_pct": 22.0,
        }
        edge = validate_llm_edge(raw, candidate)
        assert edge is not None
        assert edge.named is True

"""Shot 7 (Agent B) — the semantic LLM edge-validator.

The regex guard stack (`guards.py`, rounds 1-4) is a free pre-filter that kills
the easy, surface-form failures. This module is the *precision layer on top*:
one cheap Haiku call per candidate edge that ALREADY survived the regex
pre-filter, asking a single frozen-rubric question — does this edge's quote
genuinely support the typed relationship? It exists for the failures a regex
fundamentally cannot judge reliably ("may include a consent decree" vs "entered
a consent decree"; a product where a supplier is expected; a subtle direction
reversal).

Discipline (mirrors `bots/proportionality/fade.py`):
- Haiku ONLY (this is a filter, not a grader). Temperature 0 (the router's
  Anthropic client already pins it), JSON output, defensive parse.
- The rubric is FROZEN in code as `VALIDATOR_SYSTEM` BEFORE any full-graph run
  (handoff hard rule 3 / pre-registration). The 50+50 graded edges are a DEV
  CHECK, never a training set.
- Fail CLOSED: an unparseable, malformed, or errored validation drops the edge
  (valid=False) and is flagged — never silently kept.
- Cache by `edge_id` so re-runs and at-least-once delivery never double-spend.
- Every call is spend-capped + token-logged through the shared `LLMRouter`
  SpendTracker; an exhausted budget raises `LLMSpendCapExceeded` (never weaken
  the cap to keep going).

The validator FILTERS. It never grades the held-out reviewer sample — that is
the reviewer's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from stockbot.core.llm import (
    LLMRouter,
    LLMSpendCapExceeded,
    ModelTier,
    parse_json_object,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "frontier-edge-validator-v2"

# Quote budget fed to the prompt. Stored passages are capped at ~1200 chars by
# the candidate finder; this leaves head-room while bounding per-call cost.
MAX_QUOTE_CHARS = 1400

# Number of rubric checks. failed_check is 1..NUM_CHECKS or None.
NUM_CHECKS = 5

# ---------------------------------------------------------------------------
# FROZEN RUBRIC (pre-registered in code before any full-graph re-filter).
# Reproduces the reviewer's grading contract from reports/3b-grading-4.md /
# -3.md. Do NOT tune this to chase specific dev-check edges (handoff rule 3).
#
# v2 (single pre-registered revision after the v1 dev check): v1 was far too
# strict (24/44 correct edges wrongly killed). v1's defects, all alignment with
# the reviewer's documented contract — not edge-chasing:
#   - CHECK 2 anchored on the edge-type NAME and inverted direction (it read
#     "sole_supplier" as "the filer supplies"), killing every correct supplier
#     edge. Direction is now stated explicitly per type.
#   - CHECK 1 treated ordinary risk-factor CONSEQUENCE language ("could harm us
#     if we lost this customer") as hypothetical. Only the relationship's
#     EXISTENCE being hypothetical now disqualifies.
#   - CHECK 3 rejected government payers/agencies and EXTERNAL parents the
#     reviewer accepts; it now rejects only products, industries, geographies,
#     legal instruments, and the filer's OWN consolidated segment.
#   - CHECK 4 over-fired on quarterly current-year tables and commodity-line
#     revenue bases; it now kills only clear prior-period or sub-company
#     denominators, and leans valid when ambiguous.
# Re-frozen after v2; the v2 dev-check result is reported as measured, not tuned.
# ---------------------------------------------------------------------------

VALIDATOR_SYSTEM = (
    "You are a precise validator in a financial-graph pipeline. You are given "
    "ONE candidate edge extracted from a company's SEC annual filing: a typed "
    "relationship between the FILER (the source ticker, the company that wrote "
    "the filing) and a COUNTERPARTY, plus the verbatim QUOTE it was extracted "
    "from. The edge already passed mechanical regex guards; your job is the "
    "SEMANTIC check a regex cannot make.\n"
    "\n"
    "What each edge type MEANS (directions matter):\n"
    "- customer_concentration: the COUNTERPARTY is a major CUSTOMER — it buys "
    "from the filer / generates a large share of the filer's revenue.\n"
    "- sole_supplier: the COUNTERPARTY is the FILER'S sole or single-source "
    "SUPPLIER. Despite the type name, the COUNTERPARTY is the supplier and the "
    "FILER is the dependent buyer (the filer relies on buying from it).\n"
    "- regulatory_precedent: the COUNTERPARTY is a named regulator/agency, and "
    "the FILER is actually subject to a real enforcement action with it.\n"
    "\n"
    "Decide whether the quote GENUINELY supports the typed relationship. The "
    "edge is INVALID if ANY check below fails. Judge ONLY from the quote and the "
    "fields. The regex layer already removed clear junk, so when the quote is "
    "genuinely ambiguous, lean VALID — but apply the checks firmly when the "
    "quote clearly shows a problem.\n"
    "\n"
    "CHECK 1 — asserted, not hypothetical: the RELATIONSHIP ITSELF must be a "
    "stated fact, not hypothetical or a mere example. Risk-factor language about "
    "CONSEQUENCES ('could materially harm us', 'if we lost this customer…') does "
    "NOT disqualify — that is normal disclosure around a real relationship. "
    "INVALID only when the relationship's EXISTENCE is hypothetical/future/"
    "illustrative ('we may enter into', 'for example, restrictions may include "
    "a consent decree', 'such as'). For regulatory_precedent specifically: "
    "REQUIRE an ACTUAL action the filer is party to (entered into / operating "
    "under / received / settled a consent decree, agreement, order, or fine). A "
    "specific date is NOT required; generic descriptions of what an agency COULD "
    "impose are INVALID.\n"
    "\n"
    "CHECK 2 — direction (sole_supplier only): INVALID only if the quote shows "
    "the FILER is the one acting as the manufacturer/supplier TO the "
    "counterparty (a role reversal — the counterparty is really a customer, "
    "e.g. 'the filer will serve as the exclusive supplier of <product> to "
    "<counterparty>'). If the counterparty supplies TO the filer (the filer "
    "depends on it), this check PASSES. For customer_concentration and "
    "regulatory_precedent, this check always passes.\n"
    "\n"
    "CHECK 3 — counterparty is a real external party: INVALID only if the "
    "counterparty is NOT a real organization the filer transacts with — i.e. it "
    "is purely a PRODUCT/drug name, an INDUSTRY/sector/vertical/market label, a "
    "bare GEOGRAPHY/region, the FILER'S OWN internal segment/division/plant/"
    "system (its revenue is consolidated INTO the filer), or a LEGAL INSTRUMENT "
    "(a 'consent decree' is not an entity). VALID for: named companies "
    "(including a parent, subsidiary, or other distinct related company), named "
    "government agencies or programs used as a customer/regulator (e.g. "
    "Medicare, a federal department, 'the U.S. federal government' as a "
    "customer), and a named entity carrying a minor descriptive suffix. "
    "Distinguish internal from external: if the counterparty's OWN revenue is "
    "described as a portion of the FILER'S consolidated/total revenue "
    "(composition of self), it is INTERNAL → INVALID; if the filer SELLS TO or "
    "BUYS FROM the counterparty, it is EXTERNAL → VALID.\n"
    "\n"
    "CHECK 4 — magnitude (only when a CLAIMED MAGNITUDE is given): INVALID only "
    "if EITHER (a) the claimed % is clearly a PRIOR period while the SAME quote "
    "gives a materially different figure for a LATER period for this same "
    "counterparty (e.g. 41% in 2024 vs 0.1% in 2025 — the latest is the small "
    "one), OR (b) the denominator is clearly a SUBSET of the company (a named "
    "business segment's or subsidiary's own sales). Do NOT reject: small "
    "year-over-year drift, quarterly tables where every figure is current-year, "
    "or a company whose total revenue is simply described by its product or "
    "commodity lines (e.g. 'oil, natural gas and NGL revenues' for an oil "
    "company is its company revenue). If unsure, treat magnitude as acceptable. "
    "If no magnitude is claimed, this check passes.\n"
    "\n"
    "CHECK 5 — current, not terminated: the relationship must be CURRENT. "
    "INVALID if the quote shows it ended or is historical ('did not renew', "
    "'historically was/been', 'we have moved', 'no longer', 'former'). Resolved "
    "PAST enforcement actions are fine for regulatory_precedent (precedent "
    "legitimately references concluded actions).\n"
    "\n"
    "Respond with STRICT JSON and NOTHING else (no preamble, reason <= 25 "
    "words):\n"
    '{"valid": true|false, "failed_check": <the FIRST failing check number '
    "1-5, or null when valid>, "
    '"reason": "<one short clause citing the quote>"}'
)


@dataclass
class EdgeValidation:
    """One validator decision for one edge (logged for reviewer audit)."""

    edge_id: str
    valid: bool
    failed_check: int | None
    reason: str
    model_id: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    flags: list[str] = field(default_factory=list)
    prompt_version: str = PROMPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "valid": self.valid,
            "failed_check": self.failed_check,
            "reason": self.reason,
            "model_id": self.model_id,
            "cost_usd": round(self.cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "flags": list(self.flags),
            "prompt_version": self.prompt_version,
        }


def _magnitude_str(magnitude_pct: float | None) -> str:
    if magnitude_pct is None:
        return "n/a"
    return f"{magnitude_pct:g}% of revenue"


def build_edge_prompt(edge: dict[str, Any], *, max_quote_chars: int = MAX_QUOTE_CHARS) -> str:
    """The user-side prompt for one edge. The word JSON is present so OpenAI-
    style JSON mode (if ever routed there) is permitted; Haiku reads the rubric
    from the system prompt."""
    quote = (edge.get("quoted_passage") or "").strip()
    if len(quote) > max_quote_chars:
        quote = quote[:max_quote_chars] + " […]"
    return (
        f"EDGE TYPE: {edge.get('edge_type')}\n"
        f"SOURCE (filer) TICKER: {edge.get('from_symbol')}\n"
        f"COUNTERPARTY: {edge.get('counterparty_name')}\n"
        f"CLAIMED MAGNITUDE: {_magnitude_str(edge.get('magnitude_pct'))}\n"
        f'QUOTE: """{quote}"""\n'
        "\n"
        "Return the JSON verdict."
    )


def _coerce_failed_check(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= NUM_CHECKS else None


class FrontierEdgeValidator:
    """Haiku edge-validator: one frozen-rubric call per regex-survivor edge.

    Caches by edge_id (no double-spend), fails closed on any error/unparse, and
    spends through the shared router's SpendTracker. `LLMSpendCapExceeded`
    propagates so the caller stops rather than dropping good edges for budget.
    """

    MAX_TOKENS = 400

    def __init__(self, router: LLMRouter, *, max_quote_chars: int = MAX_QUOTE_CHARS) -> None:
        self.router = router
        self._max_quote_chars = max_quote_chars
        self._cache: dict[str, EdgeValidation] = {}

    @property
    def spend_summary(self) -> dict[str, Any]:
        return self.router.tracker.summary()

    def available(self) -> bool:
        return self.router.available(ModelTier.TRIAGE)

    def validate(self, edge: dict[str, Any]) -> EdgeValidation:
        edge_id = edge["edge_id"]
        if edge_id in self._cache:
            return self._cache[edge_id]

        prompt = build_edge_prompt(edge, max_quote_chars=self._max_quote_chars)
        try:
            resp = self.router.complete(
                ModelTier.TRIAGE, prompt, system=VALIDATOR_SYSTEM, max_tokens=self.MAX_TOKENS
            )
        except LLMSpendCapExceeded:
            # Never weaken the cap to keep going (hard rule 4). The caller stops.
            raise
        except Exception as exc:  # noqa: BLE001 — any call failure fails CLOSED.
            logger.warning("validator call failed for edge %s: %s", edge_id, exc)
            out = EdgeValidation(
                edge_id=edge_id,
                valid=False,
                failed_check=None,
                reason=f"validation_error: {exc}",
                flags=["validation_error", "fail_closed"],
            )
            self._cache[edge_id] = out
            return out

        out = self._interpret(edge_id, resp.text)
        out.model_id = resp.model_id
        out.cost_usd = resp.cost_usd
        out.input_tokens = resp.input_tokens
        out.output_tokens = resp.output_tokens
        self._cache[edge_id] = out
        return out

    @staticmethod
    def _interpret(edge_id: str, text: str) -> EdgeValidation:
        data = parse_json_object(text)
        if data is None or "valid" not in data:
            # Unparseable / schema-less response: fail closed, flag for audit.
            return EdgeValidation(
                edge_id=edge_id,
                valid=False,
                failed_check=None,
                reason="unparseable validator response",
                flags=["unparseable", "fail_closed"],
            )
        valid = bool(data.get("valid"))
        reason = str(data.get("reason", "")).strip()[:300]
        if valid:
            return EdgeValidation(edge_id=edge_id, valid=True, failed_check=None, reason=reason)
        failed_check = _coerce_failed_check(data.get("failed_check"))
        flags: list[str] = []
        if failed_check is None:
            # Invalid but no usable check number — still drop (fail closed), but
            # flag the malformed field so the reviewer can audit it.
            flags.append("missing_failed_check")
        return EdgeValidation(
            edge_id=edge_id,
            valid=False,
            failed_check=failed_check,
            reason=reason or "edge invalid (no reason given)",
            flags=flags,
        )


def estimate_validation_cost_usd(
    router: LLMRouter,
    *,
    edge_count: int,
    avg_quote_chars: int = 900,
) -> dict[str, Any]:
    """Pre-run cost estimate (≈ edge_count × per-call Haiku cost).

    Rough token model: input ≈ (system + prompt + quote) chars / 4; output
    bounded by MAX_TOKENS. Estimates run HIGH per the standing rule (2c-F2).
    """
    from stockbot.core.llm.router import estimate_cost_usd

    model_id = router.model_for(ModelTier.TRIAGE) or "claude-haiku-4-5"
    sys_chars = len(VALIDATOR_SYSTEM)
    per_call_input_tokens = (sys_chars + 160 + avg_quote_chars) // 4
    per_call_output_tokens = FrontierEdgeValidator.MAX_TOKENS  # assume full budget
    per_call = estimate_cost_usd(model_id, per_call_input_tokens, per_call_output_tokens)
    return {
        "model_id": model_id,
        "edge_count": edge_count,
        "per_call_input_tokens": per_call_input_tokens,
        "per_call_output_tokens_assumed": per_call_output_tokens,
        "per_call_cost_usd": round(per_call, 6),
        "estimated_total_usd": round(per_call * edge_count, 4),
        "cap_usd": router.tracker.cap_usd,
    }

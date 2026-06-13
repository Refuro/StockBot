"""LLM confirmation of candidate passages (Phase 3a) — extraction only.

Haiku is asked exactly three things about a regex-located passage: (a) does it
actually assert the typed edge, (b) which type, (c) the named counterparty and
percentage. It can only confirm passages the regex pass quoted — it never
invents an edge — and its answers are re-validated in code against the quoted
text (the percentage and counterparty name must literally appear in the
passage). Hard spend cap for the whole shot: $5.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from stockbot.bots.frontier.candidates import (
    CONCENTRATION_MIN_PCT,
    CUSTOMER_CONCENTRATION,
    REGULATORY_PRECEDENT,
    SOLE_SUPPLIER,
    CandidatePassage,
)

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Haiku 4.5 public list price ($ per 1M input / output tokens).
_INPUT_PER_MTOK = 1.00
_OUTPUT_PER_MTOK = 5.00

VALID_EDGE_TYPES = (CUSTOMER_CONCENTRATION, SOLE_SUPPLIER, REGULATORY_PRECEDENT)

_PROMPT_VERSION = "frontier-confirm-v1"

_INSTRUCTIONS = """\
You are an extraction checker for SEC 10-K passages. For each numbered passage,
decide whether it ASSERTS one of these relationships for the filer:

1. customer_concentration — a SINGLE customer accounted for >=20% of the filer's
   revenue/sales (not a group like "top three customers" unless one customer's
   own share is >=20%).
2. sole_supplier — the filer depends on a sole/single-source supplier for a
   material input.
3. regulatory_precedent — the filer is subject to a consent decree, deferred
   prosecution agreement, corporate integrity agreement, or named regulator
   settlement.

Rules:
- Judge ONLY from the quoted passage. Do not use outside knowledge of the company.
- counterparty_name: the named entity on the other side (customer, supplier, or
  regulator) EXACTLY as written in the passage. If the passage only says e.g.
  "one customer" / "a single supplier" without a name, set named=false and use
  that anonymous phrase as the counterparty_name.
- magnitude_pct: the disclosed revenue percentage for THAT single counterparty,
  or null if none is stated for it.
- If the passage does not clearly assert any of the three, set is_edge=false.

Respond with ONLY a JSON array, one object per passage, in input order:
[{"index": 0, "is_edge": true, "edge_type": "customer_concentration",
  "counterparty_name": "...", "named": true, "magnitude_pct": 27.0}, ...]
"""


@dataclass(frozen=True)
class ConfirmedEdge:
    edge_type: str
    counterparty_name: str
    named: bool
    magnitude_pct: float | None


class SpendCapExceeded(RuntimeError):
    """The hard LLM spend cap for the shot was reached; stop extracting."""


class EdgeConfirmer(Protocol):
    model_id: str

    def confirm_batch(
        self, filer_symbol: str, candidates: list[CandidatePassage]
    ) -> list[ConfirmedEdge | None]:
        """One result slot per candidate; None = not confirmed as an edge."""
        ...


def _passage_contains_pct(passage: str, pct: float) -> bool:
    """The claimed percentage must literally appear in the quoted passage."""
    forms = {f"{pct:g}", f"{pct:.1f}", f"{pct:.0f}"}
    for form in forms:
        if re.search(rf"\b{re.escape(form)}\s?%", passage):
            return True
    return False


def validate_llm_edge(raw: dict, candidate: CandidatePassage) -> ConfirmedEdge | None:
    """Code-side re-validation of an LLM answer against the quoted passage.

    Shot 2 checks (the percentage and the counterparty name must literally
    appear in the quote) plus the five 3a-F guards from the 3b grading and
    the 3a-G category guard from the 3b re-grade.
    """
    from stockbot.bots.frontier.guards import (
        aggregate_group_pct,
        is_anonymous_placeholder,
        is_counterparty_category,
        operative_decree,
        revenue_basis_certified,
        supplier_direction,
    )

    if not raw.get("is_edge"):
        return None
    edge_type = str(raw.get("edge_type", ""))
    if edge_type not in VALID_EDGE_TYPES:
        return None
    name = str(raw.get("counterparty_name", "")).strip()
    if not name:
        return None
    named = bool(raw.get("named"))
    magnitude = raw.get("magnitude_pct")
    magnitude_f = float(magnitude) if isinstance(magnitude, (int, float)) else None

    # The counterparty string must appear verbatim in the passage (case-insensitive).
    if name.lower() not in candidate.passage.lower():
        return None
    # Guard 5: "Customer C" style placeholders are anonymous, never named.
    if named and is_anonymous_placeholder(name):
        named = False
    # Guard 6 (3a-G): plural payor/program categories ("government healthcare
    # programs, including Medicare and Medicaid") are not tradeable entities.
    if named and is_counterparty_category(name):
        named = False
    # Concentration edges: externally-certified materiality — the >=20% figure
    # must be stated in the passage itself, not inferred by the model.
    if edge_type == CUSTOMER_CONCENTRATION:
        if magnitude_f is None or magnitude_f < CONCENTRATION_MIN_PCT:
            return None
        if not _passage_contains_pct(candidate.passage, magnitude_f):
            return None
        # Guard 1: the % must measure revenue/sales, not accounts receivable.
        if not revenue_basis_certified(candidate.passage, magnitude_f):
            return None
        # Guard 2: the % must not be an aggregate over a plural customer group.
        if aggregate_group_pct(candidate.passage, magnitude_f):
            return None
        # Guard 4: "products from X" is a supplier relation, not a customer.
        if supplier_direction(candidate.passage, name):
            return None
    # Guard 3: regulatory edges require a live decree binding the filer.
    if edge_type == REGULATORY_PRECEDENT and not operative_decree(candidate.passage):
        return None
    return ConfirmedEdge(
        edge_type=edge_type,
        counterparty_name=name,
        named=named,
        magnitude_pct=magnitude_f,
    )


def _parse_json_array(content: str) -> list[dict]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [row for row in data if isinstance(row, dict)]


class HaikuEdgeConfirmer:
    """Anthropic-backed confirmer with a hard dollar spend cap + token log."""

    prompt_version = _PROMPT_VERSION

    def __init__(
        self,
        api_key: str,
        *,
        spend_cap_usd: float = 5.0,
        batch_size: int = 8,
        model: str = HAIKU_MODEL,
    ) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for edge confirmation")
        self._api_key = api_key
        self.spend_cap_usd = spend_cap_usd
        self.batch_size = batch_size
        self.model_id = model
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.requests_made = 0

    @property
    def spend_usd(self) -> float:
        return (
            self.total_input_tokens * _INPUT_PER_MTOK
            + self.total_output_tokens * _OUTPUT_PER_MTOK
        ) / 1_000_000.0

    def _complete(self, prompt: str) -> str:
        if self.spend_usd >= self.spend_cap_usd:
            raise SpendCapExceeded(
                f"LLM spend ${self.spend_usd:.4f} reached the ${self.spend_cap_usd:.2f} cap"
            )
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        resp = client.messages.create(
            model=self.model_id,
            max_tokens=1500,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(resp, "usage", None)
        self.total_input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.total_output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.requests_made += 1
        logger.info(
            "haiku confirm request %d: in=%d out=%d cum_spend=$%.4f",
            self.requests_made,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            self.spend_usd,
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    def confirm_batch(
        self, filer_symbol: str, candidates: list[CandidatePassage]
    ) -> list[ConfirmedEdge | None]:
        results: list[ConfirmedEdge | None] = [None] * len(candidates)
        for offset in range(0, len(candidates), self.batch_size):
            chunk = candidates[offset : offset + self.batch_size]
            numbered = "\n\n".join(
                f"[{i}] (hint: {c.edge_type_hint})\n{c.passage}" for i, c in enumerate(chunk)
            )
            prompt = (
                f"{_INSTRUCTIONS}\nFiler ticker: {filer_symbol}\n\nPassages:\n\n{numbered}"
            )
            content = self._complete(prompt)
            for row in _parse_json_array(content):
                idx = row.get("index")
                if not isinstance(idx, int) or not 0 <= idx < len(chunk):
                    continue
                results[offset + idx] = validate_llm_edge(row, chunk[idx])
        return results

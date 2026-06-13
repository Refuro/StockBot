"""Ticket 2c — quality scorer: triage, ensemble, disagreement + degradation rules."""

from __future__ import annotations

import json

import pytest

from stockbot.bots.proportionality.quality import (
    DEGRADED_PROBABILITY_CAP,
    QUALITY_PROBABILITY_ENTRY_THRESHOLD,
    score_earnings_event,
)
from stockbot.core.llm import LLMRouter, LLMSpendCapExceeded, ModelTier, SpendTracker, StubLLMClient

FILING_TEXT = (
    "Item 2.02 Results of Operations. Revenue grew 18% year over year on strong demand "
    "across all segments. EPS of $1.20 beat consensus. Management raised full-year guidance. "
) * 20

PAYLOAD = {
    "actual_eps": 1.2,
    "consensus_eps": 1.0,
    "surprise_pct": 0.2,
    "filing_date": "2026-03-01",
    "accession_number": "0000000000-26-000001",
}

TRIAGE_YES = json.dumps({"worth_scoring": True, "reason": "real results"})
TRIAGE_NO = json.dumps({"worth_scoring": False, "reason": "boilerplate only"})


def _score_json(quality: float, prob: float, walkback: bool = False) -> str:
    return json.dumps(
        {
            "quality_score": quality,
            "probability_drift_up": prob,
            "one_offs": [],
            "guidance_walkback": walkback,
            "rationale": "test rationale",
        }
    )


def _router(
    *, triage: str = TRIAGE_YES, strong: str | None = None, cross: str | None = None,
    cap: float = 5.0,
) -> LLMRouter:
    clients = {ModelTier.TRIAGE: StubLLMClient("haiku-stub", default=triage)}
    if strong is not None:
        clients[ModelTier.STRONG] = StubLLMClient("opus-stub", default=strong)
    if cross is not None:
        clients[ModelTier.CROSS_CHECK] = StubLLMClient("gpt-stub", default=cross)
    return LLMRouter(clients, SpendTracker(cap_usd=cap, label="test"))


def _score(router: LLMRouter):
    return score_earnings_event(
        router, event_id="ev1", symbol="TEST", payload=PAYLOAD, filing_text=FILING_TEXT
    )


def test_triage_kills_junk_without_touching_ensemble() -> None:
    router = _router(triage=TRIAGE_NO, strong=_score_json(0.9, 0.9))
    out = _score(router)
    assert out.scored is False
    assert "triaged_out" in out.flags
    assert router.tracker.calls == 1  # only the triage call happened


def test_agreeing_ensemble_averages() -> None:
    router = _router(strong=_score_json(0.8, 0.70), cross=_score_json(0.6, 0.66))
    out = _score(router)
    assert out.scored is True
    assert out.quality_score == pytest.approx(0.7)
    assert out.stated_probability == pytest.approx(0.68)
    assert out.flags == []
    assert out.passes_entry_threshold is True


def test_disagreement_caps_at_lower_probability_and_flags() -> None:
    router = _router(strong=_score_json(0.8, 0.75), cross=_score_json(0.5, 0.40))
    out = _score(router)
    assert out.scored is True
    assert "ensemble_disagreement" in out.flags
    assert out.stated_probability == pytest.approx(0.40)
    assert "ensemble_disagreement" in out.rationale
    assert out.passes_entry_threshold is False  # 0.40 < pre-registered 0.55


def test_degradation_rule_when_openai_absent() -> None:
    router = _router(strong=_score_json(0.9, 0.85))  # no cross-check tier at all
    out = _score(router)
    assert out.scored is True
    assert "ensemble_unavailable" in out.flags
    assert out.stated_probability == pytest.approx(DEGRADED_PROBABILITY_CAP)
    assert "ensemble_unavailable" in out.rationale


def test_cross_model_midrun_failure_degrades_with_flags() -> None:
    class _Boom:
        model_id = "gpt-stub"

        def complete(self, prompt, *, system=None, max_tokens=1024):
            raise RuntimeError("429 insufficient_quota")

    clients = {
        ModelTier.TRIAGE: StubLLMClient("haiku-stub", default=TRIAGE_YES),
        ModelTier.STRONG: StubLLMClient("opus-stub", default=_score_json(0.9, 0.9)),
        ModelTier.CROSS_CHECK: _Boom(),
    }
    router = LLMRouter(clients, SpendTracker(cap_usd=5.0, label="test"))
    out = _score(router)
    assert out.scored is True
    assert "cross_model_failed" in out.flags
    assert "ensemble_unavailable" in out.flags
    assert out.stated_probability <= DEGRADED_PROBABILITY_CAP


def test_spend_cap_propagates_out_of_scoring() -> None:
    router = _router(strong=_score_json(0.9, 0.9), cap=0.0)
    router.tracker.spent_usd = 1.0  # cap already busted
    with pytest.raises(LLMSpendCapExceeded):
        _score(router)


def test_empty_filing_text_is_not_scored() -> None:
    router = _router(strong=_score_json(0.9, 0.9))
    out = score_earnings_event(
        router, event_id="ev2", symbol="TEST", payload=PAYLOAD, filing_text="  "
    )
    assert out.scored is False
    assert "no_filing_text" in out.flags
    assert router.tracker.calls == 0


def test_entry_threshold_is_preregistered_value() -> None:
    # Hard rule 4: this number was written before the validation sample was
    # scored. If it changes, the change must be documented in the shot report.
    assert QUALITY_PROBABILITY_ENTRY_THRESHOLD == 0.55

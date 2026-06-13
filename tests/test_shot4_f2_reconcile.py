"""Ticket 2c-F2 — spend-reconciliation script unit tests (offline)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; load the module directly.
_SPEC = importlib.util.spec_from_file_location(
    "reconcile_llm_spend",
    Path(__file__).resolve().parent.parent / "scripts" / "reconcile_llm_spend.py",
)
reconcile_llm_spend = importlib.util.module_from_spec(_SPEC)
sys.modules["reconcile_llm_spend"] = reconcile_llm_spend
_SPEC.loader.exec_module(reconcile_llm_spend)


SPEND = {
    "label": "test",
    "spent_usd": 3.0288,
    "calls": 38,
    "by_model": {
        "claude-haiku-4-5-20251001": 0.1113,
        "claude-opus-4-8": 2.0893,
        "gpt-5.5": 0.8283,
    },
}


def test_reconcile_groups_by_provider_and_computes_ratio() -> None:
    out = reconcile_llm_spend.reconcile(SPEND, {"anthropic": 0.81, "openai": 0.37},
                                        incomplete={"openai"})
    a = out["anthropic"]
    assert a["estimated_usd"] == pytest.approx(2.2006)
    assert a["ratio_estimated_over_real"] == pytest.approx(2.2006 / 0.81, rel=1e-6)
    # Implied prices = current constants / blended ratio.
    assert "claude-opus-4-8" in a["implied_real_price_per_mtok"]

    o = out["openai"]
    assert o["incomplete"] is True
    # Lag-truncated readings must NEVER yield recalibration numbers.
    assert o["implied_real_price_per_mtok"] == {}


def test_extract_spend_accepts_embedded_llm_spend_block() -> None:
    doc = {"anything": 1, "llm_spend": SPEND}
    assert reconcile_llm_spend.extract_spend(doc)["by_model"] == SPEND["by_model"]
    assert reconcile_llm_spend.extract_spend(SPEND) is SPEND
    with pytest.raises(ValueError):
        reconcile_llm_spend.extract_spend({"nope": 1})


def test_missing_delta_is_skipped_not_invented() -> None:
    out = reconcile_llm_spend.reconcile(SPEND, {"anthropic": 0.81})
    assert out["openai"]["real_usd"] is None
    assert out["openai"]["ratio_estimated_over_real"] is None

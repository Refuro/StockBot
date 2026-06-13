"""Ticket 2c-F2 — monthly LLM spend reconciliation (the ops habit).

Compares a run's tracker-estimated spend (the `llm_spend` block any run summary
or study report carries) against the REAL deltas read off the two provider
billing dashboards, per provider, and prints the implied real $/MTok so
`PRICE_PER_MTOK` in `stockbot/core/llm/router.py` can be recalibrated from
measurements instead of guesses.

Usage (monthly, or after any controlled spend test):
    python scripts/reconcile_llm_spend.py reports/spend-reconciliation-test.json \
        --anthropic-delta 0.81 --openai-delta 0.37

Omit a --*-delta flag to be prompted for it. Pass --openai-delta-incomplete if
the OpenAI dashboard reading was taken before spend settled (it lags by hours):
the comparison is still printed, but the implied prices are marked UNUSABLE so
nobody recalibrates from a truncated number.

THE STANDING RULE (2c-F2, do not weaken): tracker estimates feed spend CAPS,
never billing. Estimates may run HIGH — a cap that bites early is safe — but
must never knowingly run LOW. Recalibrate only from CLEAN dashboard deltas
(fully settled, no concurrent unrelated usage), and pad the constants above
the implied real price rather than matching it exactly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make `stockbot` importable when run as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockbot.core.llm.router import PRICE_PER_MTOK  # noqa: E402


def provider_for_model(model_id: str) -> str:
    if model_id.startswith("claude"):
        return "anthropic"
    if model_id.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    return "unknown"


def extract_spend(doc: dict[str, Any]) -> dict[str, Any]:
    """Accept either a bare spend block or any report that embeds `llm_spend`."""
    if "by_model" in doc:
        return doc
    if "llm_spend" in doc and isinstance(doc["llm_spend"], dict):
        return doc["llm_spend"]
    raise ValueError("no llm_spend block found in the input JSON")


def reconcile(
    spend: dict[str, Any],
    real_deltas: dict[str, float],
    *,
    incomplete: set[str] = frozenset(),
) -> dict[str, Any]:
    """Per provider: estimated vs real, ratio, and implied real $/MTok per model.

    The implied prices scale each model's CURRENT constants by the provider's
    blended estimated/real ratio — exact when one model dominates the provider's
    spend, approximate otherwise (the caveat is included in the output).
    """
    by_model: dict[str, float] = spend.get("by_model", {})
    providers: dict[str, dict[str, Any]] = {}
    for model_id, est_cost in by_model.items():
        p = provider_for_model(model_id)
        entry = providers.setdefault(p, {"estimated_usd": 0.0, "models": []})
        entry["estimated_usd"] += float(est_cost)
        entry["models"].append(model_id)

    for p, entry in providers.items():
        real = real_deltas.get(p)
        entry["real_usd"] = real
        entry["incomplete"] = p in incomplete
        if real is None or entry["estimated_usd"] <= 0:
            entry["ratio_estimated_over_real"] = None
            entry["implied_real_price_per_mtok"] = {}
            continue
        ratio = entry["estimated_usd"] / real if real > 0 else None
        entry["ratio_estimated_over_real"] = ratio
        implied: dict[str, tuple[float, float]] = {}
        if ratio and not entry["incomplete"]:
            for model_id in entry["models"]:
                cur = PRICE_PER_MTOK.get(model_id)
                if cur is None:
                    continue
                implied[model_id] = (round(cur[0] / ratio, 2), round(cur[1] / ratio, 2))
        entry["implied_real_price_per_mtok"] = implied
    return providers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile tracker-estimated LLM spend vs dashboards")
    parser.add_argument("spend_json", help="run summary / report JSON containing an llm_spend block")
    parser.add_argument("--anthropic-delta", type=float, default=None,
                        help="real Anthropic dashboard delta for the run window (USD)")
    parser.add_argument("--openai-delta", type=float, default=None,
                        help="real OpenAI dashboard delta for the run window (USD)")
    parser.add_argument("--openai-delta-incomplete", action="store_true",
                        help="the OpenAI reading was taken before spend settled (dashboard lag)")
    args = parser.parse_args(argv)

    doc = json.loads(Path(args.spend_json).read_text(encoding="utf-8"))
    spend = extract_spend(doc)

    deltas: dict[str, float] = {}
    for provider, value in (("anthropic", args.anthropic_delta), ("openai", args.openai_delta)):
        if value is None:
            raw = input(f"Real {provider} dashboard delta for this run window (USD, blank to skip): ").strip()
            if not raw:
                continue
            value = float(raw)
        deltas[provider] = value

    incomplete = {"openai"} if args.openai_delta_incomplete else set()
    providers = reconcile(spend, deltas, incomplete=incomplete)

    print(f"\nSpend label: {spend.get('label', '?')}  (tracker total ${spend.get('spent_usd', '?')}, "
          f"{spend.get('calls', '?')} calls)")
    for p, e in sorted(providers.items()):
        print(f"\n== {p} ==")
        print(f"  estimated: ${e['estimated_usd']:.4f}   ({', '.join(e['models'])})")
        if e["real_usd"] is None:
            print("  real:      (not provided — skipped)")
            continue
        tag = "  [INCOMPLETE — dashboard lag; UNUSABLE for recalibration]" if e["incomplete"] else ""
        print(f"  real:      ${e['real_usd']:.4f}{tag}")
        ratio = e["ratio_estimated_over_real"]
        if ratio:
            direction = "HIGH (safe)" if ratio > 1 else "LOW (fix immediately)"
            print(f"  ratio:     estimates run {ratio:.2f}x {direction}")
        for model_id, (inp, outp) in e["implied_real_price_per_mtok"].items():
            cur = PRICE_PER_MTOK[model_id]
            print(f"  implied real $/MTok for {model_id}: ({inp}, {outp})  "
                  f"[currently ({cur[0]}, {cur[1]}); blended-ratio estimate — exact only "
                  f"if this model dominates the provider's spend]")
    print(
        "\nStanding rule: estimates feed CAPS, never billing. They may run high "
        "(caps bite early — safe) but never knowingly low. Recalibrate only from "
        "CLEAN deltas, and pad constants ABOVE the implied real price."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

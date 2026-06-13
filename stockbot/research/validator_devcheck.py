"""Shot 7 (Agent B) — dev-check for the FrontierEdgeValidator.

Runs the FROZEN-rubric Haiku validator on the 50 round-4 labeled edges
(`reports/3b-precision-sample-4.md` + the reviewer's verdicts in
`reports/3b-grading-4.md`) and reports a confusion matrix vs those verdicts.

This is a HELD-BACK DEV CHECK, not a training set (handoff rule 3): the rubric
was frozen in `validator.py` before this ran. We inspect the matrix only to
confirm the rubric is sane (kills the 6 round-4 failures, keeps most of the 44
correct). We do NOT iterate the rubric to chase specific edges, and we never
look at the held-out seed-3005 survivors.

Run: python -m stockbot.research.validator_devcheck --cap 1.5
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from stockbot.bots.frontier.validator import FrontierEdgeValidator
from stockbot.core.llm import build_router

# Pre-registered from reports/3b-grading-4.md — the 6 INCORRECT edges by their
# index in 3b-precision-sample-4.md. (Grading-4's "#2 AQST/Pharmanovia" is the
# sole_supplier edge, which is sample index 3; #1/#7/#20/#27/#50 align directly.)
ROUND4_INCORRECT_INDICES = frozenset({1, 3, 7, 20, 27, 50})

_HEADER_RE = re.compile(r"^##\s+(\d+)\.\s+`([^`]+)`\s+--\[([^\]]+)\]-->\s+(.+?)\s*$")
_MAG_RE = re.compile(r"\*\*Magnitude:\*\*\s+([\d.]+)%")
_RESOLVED_RE = re.compile(r"\s*\(resolved:\s*`([^`]+)`\)\s*$")


def parse_precision_sample(path: Path) -> list[dict[str, Any]]:
    """Parse a 3b-precision-sample-*.md file into edge dicts."""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=##\s+\d+\.)", text)
    edges: list[dict[str, Any]] = []
    for block in blocks:
        lines = block.splitlines()
        header = next((m for ln in lines if (m := _HEADER_RE.match(ln))), None)
        if header is None:
            continue
        idx = int(header.group(1))
        ticker = header.group(2)
        edge_type = header.group(3)
        counterparty = header.group(4)
        symbol = None
        rm = _RESOLVED_RE.search(counterparty)
        if rm:
            symbol = rm.group(1)
            counterparty = _RESOLVED_RE.sub("", counterparty).strip()
        magnitude = None
        mag = next((m for ln in lines if (m := _MAG_RE.search(ln))), None)
        if mag:
            magnitude = float(mag.group(1))
        quote = ""
        for ln in lines:
            if ln.startswith("> "):
                quote = ln[2:].strip()
                break
        edges.append(
            {
                "edge_id": f"sample4-{idx}",
                "index": idx,
                "edge_type": edge_type,
                "from_symbol": ticker,
                "counterparty_name": counterparty,
                "counterparty_symbol": symbol,
                "magnitude_pct": magnitude,
                "quoted_passage": quote,
            }
        )
    return edges


def run_devcheck(
    sample_path: Path,
    incorrect_indices: frozenset[int],
    *,
    cap_usd: float = 1.5,
) -> dict[str, Any]:
    edges = parse_precision_sample(sample_path)
    router = build_router(cap_usd=cap_usd, label="frontier-validator-devcheck")
    validator = FrontierEdgeValidator(router)
    if not validator.available():
        raise RuntimeError("ANTHROPIC_API_KEY required for the dev-check (Haiku validator)")

    tp = fp = tn = fn = 0
    per_edge: list[dict[str, Any]] = []
    caught_failures: list[int] = []
    missed_failures: list[int] = []
    wrongly_killed: list[int] = []
    for edge in edges:
        idx = edge["index"]
        reviewer_incorrect = idx in incorrect_indices
        verdict = validator.validate(edge)
        validator_invalid = not verdict.valid
        if reviewer_incorrect and validator_invalid:
            tp += 1
            caught_failures.append(idx)
        elif reviewer_incorrect and not validator_invalid:
            fn += 1
            missed_failures.append(idx)
        elif not reviewer_incorrect and validator_invalid:
            fp += 1
            wrongly_killed.append(idx)
        else:
            tn += 1
        per_edge.append(
            {
                "index": idx,
                "edge": f"{edge['from_symbol']} -[{edge['edge_type']}]-> {edge['counterparty_name']}",
                "reviewer_verdict": "INCORRECT" if reviewer_incorrect else "CORRECT",
                "validator_valid": verdict.valid,
                "failed_check": verdict.failed_check,
                "reason": verdict.reason,
                "flags": verdict.flags,
                "cost_usd": round(verdict.cost_usd, 6),
            }
        )

    n_correct = len(edges) - len(incorrect_indices)
    return {
        "sample": str(sample_path),
        "n_edges": len(edges),
        "n_reviewer_incorrect": len(incorrect_indices),
        "n_reviewer_correct": n_correct,
        "confusion_matrix": {
            "true_positive_killed_bad": tp,
            "false_negative_missed_bad": fn,
            "true_negative_kept_good": tn,
            "false_positive_killed_good": fp,
        },
        "recall_on_failures": round(tp / len(incorrect_indices), 3) if incorrect_indices else None,
        "specificity_on_correct": round(tn / n_correct, 3) if n_correct else None,
        "caught_failures_idx": sorted(caught_failures),
        "missed_failures_idx": sorted(missed_failures),
        "wrongly_killed_correct_idx": sorted(wrongly_killed),
        "spend": validator.spend_summary,
        "per_edge": per_edge,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FrontierEdgeValidator dev-check")
    parser.add_argument("--sample", default="reports/3b-precision-sample-4.md")
    parser.add_argument("--out", default="reports/shot7-validator-devcheck.json")
    parser.add_argument("--cap", type=float, default=1.5)
    args = parser.parse_args(argv)

    result = run_devcheck(
        Path(args.sample), ROUND4_INCORRECT_INDICES, cap_usd=args.cap
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    cm = result["confusion_matrix"]
    print(
        f"dev-check: killed {cm['true_positive_killed_bad']}/{result['n_reviewer_incorrect']} "
        f"failures, kept {cm['true_negative_kept_good']}/{result['n_reviewer_correct']} correct "
        f"(FP={cm['false_positive_killed_good']}, FN={cm['false_negative_missed_bad']}); "
        f"spend ${result['spend']['spent_usd']:.4f}"
    )
    print(f"  caught: {result['caught_failures_idx']}  missed: {result['missed_failures_idx']}")
    print(f"  wrongly killed correct: {result['wrongly_killed_correct_idx']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

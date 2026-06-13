"""3c-prep-2: re-filter the stored frontier edges.

Two pipelines over `data/state.db` (quotes are always retained):
- `refilter_graph` (round 3, no LLM): the original metadata pass — kill, demote
  (named->0), and null G5 segment magnitudes via the guard stack.
- `combined_refilter` (shot 7): the three-stage precision pipeline —
    1. regex guard stack (rounds 1-4, free) — kills the easy cases;
    2. dedupe — collapse duplicate/dual-class/parent-subsidiary edges;
    3. Haiku edge-validator — the semantic precision layer on the survivors.
  Writes `reports/3c-prep-2-refilter-stats-v2.json` and a per-edge validator
  decision log for reviewer audit.

Run (round 3):  python -m stockbot.research.refilter_graph [--db data/state.db]
Run (shot 7):   python -m stockbot.research.refilter_graph --combined --validator-cap 3.0
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stockbot.bots.frontier.dedupe import dedupe_edges
from stockbot.bots.frontier.guards import apply_edge_guard_stack
from stockbot.bots.frontier.validator import (
    FrontierEdgeValidator,
    estimate_validation_cost_usd,
)
from stockbot.core.llm import LLMSpendCapExceeded, build_router
from stockbot.core.state.store import StateStore

logger = logging.getLogger(__name__)


def _edge_counts(edges: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: Counter[str] = Counter()
    named = 0
    for e in edges:
        by_type[e["edge_type"]] += 1
        if e["named"]:
            named += 1
    return {
        "total": len(edges),
        "named": named,
        "anonymous": len(edges) - named,
        "by_type": dict(sorted(by_type.items())),
    }


def refilter_graph(store: StateStore) -> dict[str, Any]:
    before_edges = store.get_frontier_edges()
    before = _edge_counts(before_edges)
    kills: Counter[str] = Counter()
    demotions: Counter[str] = Counter()
    magnitude_nulled = 0
    deleted = 0

    for edge in before_edges:
        decision = apply_edge_guard_stack(
            edge_type=edge["edge_type"],
            counterparty_name=edge["counterparty_name"],
            named=edge["named"],
            magnitude_pct=edge["magnitude_pct"],
            quoted_passage=edge["quoted_passage"],
        )
        if decision.action == "kill":
            guard = decision.guard or "unknown"
            kills[guard] += 1
            store.delete_frontier_edge(edge["edge_id"])
            deleted += 1
            continue
        updates: dict[str, Any] = {}
        if decision.action == "demote" and edge["named"]:
            demotions[decision.demoted_by or "unknown"] += 1
            updates["named"] = False
        if decision.null_magnitude and edge["magnitude_pct"] is not None:
            magnitude_nulled += 1
            updates["magnitude_pct"] = None
        if updates:
            store.update_frontier_edge(edge["edge_id"], **updates)

    after_edges = store.get_frontier_edges()
    after = _edge_counts(after_edges)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "after": after,
        "deleted": deleted,
        "kills_by_guard": dict(sorted(kills.items(), key=lambda kv: (-kv[1], kv[0]))),
        "demotions_by_guard": dict(sorted(demotions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "magnitude_nulled": magnitude_nulled,
    }


# ---------------------------------------------------------------------------
# Shot 7 — combined three-stage re-filter (regex -> dedupe -> LLM validator).
# ---------------------------------------------------------------------------


def _apply_regex_stage(
    store: StateStore, edges: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], Counter, Counter, int]:
    """Stage 1: regex guard stack. Returns (survivors, kills, demotions, nulled).

    Mutates the store (deletes kills, flips demotions, nulls G5 magnitudes) and
    mirrors those edits onto the in-memory survivor dicts for the next stages.
    """
    kills: Counter[str] = Counter()
    demotions: Counter[str] = Counter()
    nulled = 0
    survivors: list[dict[str, Any]] = []
    for edge in edges:
        decision = apply_edge_guard_stack(
            edge_type=edge["edge_type"],
            counterparty_name=edge["counterparty_name"],
            named=edge["named"],
            magnitude_pct=edge["magnitude_pct"],
            quoted_passage=edge["quoted_passage"],
        )
        if decision.action == "kill":
            kills[decision.guard or "unknown"] += 1
            store.delete_frontier_edge(edge["edge_id"])
            continue
        updates: dict[str, Any] = {}
        if decision.action == "demote" and edge["named"]:
            demotions[decision.demoted_by or "unknown"] += 1
            updates["named"] = False
            edge["named"] = False
        if decision.null_magnitude and edge["magnitude_pct"] is not None:
            nulled += 1
            updates["magnitude_pct"] = None
            edge["magnitude_pct"] = None
        if updates:
            store.update_frontier_edge(edge["edge_id"], **updates)
        survivors.append(edge)
    return survivors, kills, demotions, nulled


def _apply_dedupe_stage(
    store: StateStore, edges: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stage 2: collapse duplicates. Deletes the merged-away edges. Returns
    (survivors, merge_records-as-dicts)."""
    survivors, merges = dedupe_edges(edges)
    kept_ids = {e["edge_id"] for e in survivors}
    for edge in edges:
        if edge["edge_id"] not in kept_ids:
            store.delete_frontier_edge(edge["edge_id"])
    for m in merges:
        logger.info(
            "dedupe merge [%s]: kept %s drop %s (%s -> %s)",
            m.rule, m.kept_edge_id, m.dropped_edge_id,
            m.dropped_counterparty, m.kept_counterparty,
        )
    return survivors, [m.to_dict() for m in merges]


def _apply_validator_stage(
    store: StateStore,
    edges: list[dict[str, Any]],
    validator: FrontierEdgeValidator,
) -> dict[str, Any]:
    """Stage 3: Haiku semantic validator on the survivors. Drops invalid edges
    (fail-closed), returns kills-by-check, the per-edge log, and spend state."""
    kills: Counter[str] = Counter()
    log: list[dict[str, Any]] = []
    validated = 0
    spend_exhausted = False
    survivors: list[dict[str, Any]] = []
    for edge in edges:
        try:
            verdict = validator.validate(edge)
        except LLMSpendCapExceeded as exc:
            logger.warning("validator spend cap reached: %s", exc)
            spend_exhausted = True
            break
        validated += 1
        entry = verdict.to_dict()
        entry.update(
            {
                "from_symbol": edge["from_symbol"],
                "edge_type": edge["edge_type"],
                "counterparty_name": edge["counterparty_name"],
            }
        )
        log.append(entry)
        if verdict.valid:
            survivors.append(edge)
        else:
            bucket = (
                f"check_{verdict.failed_check}"
                if verdict.failed_check
                else "unparseable_or_error"
            )
            kills[bucket] += 1
            store.delete_frontier_edge(edge["edge_id"])
    # Edges left unvalidated by an exhausted budget are NOT dropped — kept and
    # flagged so the count is honest rather than silently lost to cost.
    unvalidated = edges[validated:] if spend_exhausted else []
    survivors.extend(unvalidated)
    return {
        "kills_by_check": dict(sorted(kills.items())),
        "log": log,
        "validated": validated,
        "spend_exhausted": spend_exhausted,
        "unvalidated_kept": [e["edge_id"] for e in unvalidated],
        "survivors": survivors,
    }


def combined_refilter(
    store: StateStore,
    *,
    validator: FrontierEdgeValidator | None,
    validator_log_path: Path | None = None,
) -> dict[str, Any]:
    """Shot 7 three-stage re-filter. Returns the v2 stats dict."""
    before_edges = store.get_frontier_edges()
    before = _edge_counts(before_edges)

    survivors, kills, demotions, nulled = _apply_regex_stage(store, before_edges)
    after_regex = _edge_counts(survivors)

    deduped, merges = _apply_dedupe_stage(store, survivors)
    after_dedupe = _edge_counts(deduped)

    # The validator is the NAMED-pool precision layer; anonymous edges are
    # stats-only (never graph edges) and pass through unvalidated to save spend.
    named_survivors = [e for e in deduped if e["named"]]
    anon_survivors = [e for e in deduped if not e["named"]]

    validator_section: dict[str, Any]
    if validator is not None and validator.available():
        estimate = estimate_validation_cost_usd(
            validator.router, edge_count=len(named_survivors)
        )
        logger.info("validator estimate (named only): %s", estimate)
        result = _apply_validator_stage(store, named_survivors, validator)
        final_survivors = result.pop("survivors") + anon_survivors
        if validator_log_path is not None:
            validator_log_path.parent.mkdir(parents=True, exist_ok=True)
            validator_log_path.write_text(
                "\n".join(json.dumps(e) for e in result["log"]), encoding="utf-8"
            )
        validator_section = {
            "ran": True,
            "estimate": estimate,
            "spend": validator.spend_summary,
            "validator_log": str(validator_log_path) if validator_log_path else None,
            **{k: v for k, v in result.items() if k != "log"},
        }
    else:
        final_survivors = deduped
        validator_section = {
            "ran": False,
            "reason": "validator unavailable (no ANTHROPIC_API_KEY) — regex+dedupe only",
        }

    after_final = _edge_counts(final_survivors)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": ["regex_guard_stack", "dedupe", "llm_validator"],
        "before": before,
        "after_regex": after_regex,
        "after_dedupe": after_dedupe,
        "after_final": after_final,
        "regex_kills_by_guard": dict(sorted(kills.items(), key=lambda kv: (-kv[1], kv[0]))),
        "regex_demotions_by_guard": dict(
            sorted(demotions.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "regex_magnitude_nulled": nulled,
        "dedupe_merges": merges,
        "dedupe_merge_count": len(merges),
        "validator": validator_section,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-filter frontier graph with guard stack")
    parser.add_argument("--db", default="data/state.db")
    parser.add_argument("--stats-out", default="reports/3c-prep-2-refilter-stats.json")
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Shot 7: regex -> dedupe -> Haiku validator (writes the v2 stats).",
    )
    parser.add_argument("--validator-cap", type=float, default=3.0)
    parser.add_argument(
        "--combined-stats-out", default="reports/3c-prep-2-refilter-stats-v2.json"
    )
    parser.add_argument("--validator-log-out", default="reports/shot7-validator-log.jsonl")
    parser.add_argument(
        "--no-llm", action="store_true", help="combined pipeline without the LLM stage"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = StateStore(args.db)

    if args.combined:
        validator = None
        if not args.no_llm:
            router = build_router(cap_usd=args.validator_cap, label="frontier-validator")
            validator = FrontierEdgeValidator(router)
        stats = combined_refilter(
            store, validator=validator, validator_log_path=Path(args.validator_log_out)
        )
        out = Path(args.combined_stats_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(json.dumps(stats, indent=2))
        return 0

    stats = refilter_graph(store)
    out = Path(args.stats_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

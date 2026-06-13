"""Consensus + novelty scoring from daily Flow Lens samples."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TickerConsensus:
    symbol: str
    consensus_score: float
    novelty_flag: bool
    model_count: int
    prompt_count: int
    models: tuple[str, ...]


def compute_daily_consensus(
    samples: list[dict],
    *,
    total_models: int,
    prior_consensus_symbols: set[str] | None = None,
    consensus_threshold: float = 0.5,
) -> list[TickerConsensus]:
    """Aggregate per-ticker consensus from raw samples for one logged day.

    ``consensus_score`` = distinct models mentioning the symbol / ``total_models``.
    ``novelty_flag`` = symbol newly crosses ``consensus_threshold`` vs prior day set.
    """
    if total_models <= 0:
        return []

    by_symbol_models: dict[str, set[str]] = {}
    by_symbol_prompts: dict[str, set[str]] = {}
    for sample in samples:
        model_id = str(sample["model_id"])
        prompt_id = str(sample["prompt_id"])
        for symbol in sample.get("tickers") or []:
            sym = str(symbol).upper()
            by_symbol_models.setdefault(sym, set()).add(model_id)
            by_symbol_prompts.setdefault(sym, set()).add(f"{model_id}:{prompt_id}")

    prior = prior_consensus_symbols or set()
    rows: list[TickerConsensus] = []
    for symbol in sorted(by_symbol_models):
        model_count = len(by_symbol_models[symbol])
        score = model_count / total_models
        in_consensus_today = score >= consensus_threshold
        was_in_consensus = symbol in prior
        novelty = in_consensus_today and not was_in_consensus
        rows.append(
            TickerConsensus(
                symbol=symbol,
                consensus_score=score,
                novelty_flag=novelty,
                model_count=model_count,
                prompt_count=len(by_symbol_prompts[symbol]),
                models=tuple(sorted(by_symbol_models[symbol])),
            )
        )
    return rows

"""Daily Flow Lens run — query consumer models and persist consensus."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from stockbot.config import AppConfig, FlowLensConfig, load_config
from stockbot.core.state.store import StateStore
from stockbot.tools.flow_lens.consensus import compute_daily_consensus
from stockbot.tools.flow_lens.extract import extract_tickers
from stockbot.tools.flow_lens.prompts import RETAIL_PROMPT_ARCHETYPES
from stockbot.tools.flow_lens.providers import (
    ConsumerModelClient,
    ConsumerModelSpec,
    DEFAULT_CONSUMER_MODELS,
    available_providers,
    build_consumer_client,
    estimate_cost_usd,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlowLensRunResult:
    logged_date: str
    samples_written: int
    consensus_rows: int
    novel_tickers: tuple[str, ...]
    models_run: tuple[str, ...] = ()
    models_skipped: tuple[str, ...] = ()
    models_failed: tuple[str, ...] = ()
    estimated_cost_usd: float = 0.0


def run_daily_flow_lens(
    config: AppConfig | None = None,
    *,
    logged_date: date | None = None,
    client: ConsumerModelClient | None = None,
    offline: bool = False,
) -> FlowLensRunResult:
    """Sample all configured consumer models × retail prompts for one day."""
    cfg = config or load_config()
    fl_cfg = cfg.flow_lens
    day = logged_date or datetime.now(timezone.utc).date()
    day_str = day.isoformat()

    store = StateStore(cfg.state.db_path)
    provider = client or build_consumer_client(offline=offline)
    models = _resolve_models(fl_cfg)

    # Live runs (no injected client, not offline) use ONLY providers we hold
    # keys for — unavailable consumer models are skipped cleanly, never stubbed
    # into the production log (stub data would poison the F1 dataset).
    skipped: tuple[str, ...] = ()
    if client is None and not offline:
        usable = available_providers()
        skipped = tuple(m.model_id for m in models if m.provider not in usable)
        models = tuple(m for m in models if m.provider in usable)

    samples_written = 0
    raw_samples: list[dict] = []
    estimated_cost = 0.0
    succeeded: list[ConsumerModelSpec] = []
    failed: list[str] = []
    sampled_at = datetime.now(timezone.utc)

    for model in models:
        # Collect the whole model's batch before persisting: a provider that
        # fails mid-run (quota, outage) is dropped entirely rather than
        # contributing a partial, consensus-skewing sample set.
        completions: list[tuple[object, object]] = []
        try:
            for prompt in RETAIL_PROMPT_ARCHETYPES:
                completion = provider.complete(model, prompt.text)
                estimated_cost += estimate_cost_usd(
                    model.model, completion.input_tokens, completion.output_tokens
                )
                completions.append((prompt, completion))
        except Exception as exc:  # provider SDK errors vary; one model must not kill the run
            logger.warning("flow lens provider %r failed, skipping: %s", model.model_id, exc)
            failed.append(model.model_id)
            continue

        succeeded.append(model)
        for prompt, completion in completions:
            tickers = extract_tickers(completion.content)
            sample_id = str(uuid.uuid4())
            store.record_flow_lens_sample(
                sample_id=sample_id,
                logged_date=day_str,
                model_id=completion.model_id,
                model_version=completion.model_version,
                prompt_id=prompt.prompt_id,
                prompt_text=prompt.text,
                response_text=completion.content,
                tickers=tickers,
                sampled_at=sampled_at,
            )
            samples_written += 1
            raw_samples.append(
                {
                    "model_id": completion.model_id,
                    "prompt_id": prompt.prompt_id,
                    "tickers": tickers,
                }
            )

    if models and not succeeded:
        raise RuntimeError(f"all flow lens providers failed: {failed}")

    prior_symbols = store.get_flow_lens_consensus_symbols(
        _prior_date(day),
        min_score=fl_cfg.consensus_threshold,
    )
    consensus_rows = compute_daily_consensus(
        raw_samples,
        total_models=len(succeeded),
        prior_consensus_symbols=set(prior_symbols),
        consensus_threshold=fl_cfg.consensus_threshold,
    )
    for row in consensus_rows:
        store.record_flow_lens_consensus(
            logged_date=day_str,
            symbol=row.symbol,
            consensus_score=row.consensus_score,
            novelty_flag=row.novelty_flag,
            model_count=row.model_count,
            prompt_count=row.prompt_count,
            models=list(row.models),
        )

    novel = tuple(sorted(r.symbol for r in consensus_rows if r.novelty_flag))
    return FlowLensRunResult(
        logged_date=day_str,
        samples_written=samples_written,
        consensus_rows=len(consensus_rows),
        novel_tickers=novel,
        models_run=tuple(m.model_id for m in succeeded),
        models_skipped=skipped,
        models_failed=tuple(failed),
        estimated_cost_usd=round(estimated_cost, 4),
    )


def _resolve_models(fl_cfg: FlowLensConfig) -> tuple[ConsumerModelSpec, ...]:
    if not fl_cfg.consumer_models:
        return DEFAULT_CONSUMER_MODELS
    out: list[ConsumerModelSpec] = []
    for row in fl_cfg.consumer_models:
        out.append(
            ConsumerModelSpec(
                model_id=str(row["model_id"]),
                provider=str(row["provider"]),
                model=str(row["model"]),
            )
        )
    return tuple(out)


def _prior_date(day: date) -> str:
    return (day - timedelta(days=1)).isoformat()

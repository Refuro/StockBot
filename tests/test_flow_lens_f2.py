"""Tests for Workstream F2 — flow validation study."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from stockbot.config import AppConfig, load_config
from stockbot.core.state.store import StateStore
from stockbot.tools.flow_lens.f2_study import run_flow_validation_study


def _seed_consensus(store: StateStore) -> None:
    # Day 1 baseline consensus (not novel)
    store.record_flow_lens_consensus(
        logged_date="2026-06-01",
        symbol="AAPL",
        consensus_score=0.75,
        novelty_flag=True,
        model_count=3,
        prompt_count=10,
        models=["chatgpt", "claude", "gemini"],
    )
    # Day 2: AAPL not novel, TSLA newly enters consensus.
    store.record_flow_lens_consensus(
        logged_date="2026-06-02",
        symbol="AAPL",
        consensus_score=0.75,
        novelty_flag=False,
        model_count=3,
        prompt_count=11,
        models=["chatgpt", "claude", "gemini"],
    )
    store.record_flow_lens_consensus(
        logged_date="2026-06-02",
        symbol="TSLA",
        consensus_score=0.50,
        novelty_flag=True,
        model_count=2,
        prompt_count=7,
        models=["chatgpt", "grok"],
    )


def _write_finra_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "date,symbol,off_exchange_volume",
                # AAPL baseline around 100, then mild uptick after 6/2
                "2026-05-31,AAPL,100",
                "2026-06-01,AAPL,100",
                "2026-06-02,AAPL,100",
                "2026-06-03,AAPL,120",
                "2026-06-04,AAPL,125",
                "2026-06-05,AAPL,130",
                # TSLA baseline around 80, then strong uplift after novelty onset 6/2
                "2026-05-31,TSLA,80",
                "2026-06-01,TSLA,80",
                "2026-06-02,TSLA,80",
                "2026-06-03,TSLA,140",
                "2026-06-04,TSLA,150",
                "2026-06-05,TSLA,160",
            ]
        ),
        encoding="utf-8",
    )


def test_f2_study_computes_positive_effect_sizes(tmp_path: Path):
    db = tmp_path / "flow_f2.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})
    store = StateStore(db)
    _seed_consensus(store)

    finra_dir = tmp_path / "finra"
    finra_dir.mkdir(parents=True, exist_ok=True)
    _write_finra_csv(finra_dir / "off_exchange.csv")

    result = run_flow_validation_study(
        finra_csv_dir=finra_dir,
        config=cfg,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 5),
        lookback_days=2,
        max_horizon_days=3,
    )
    assert result.onset_count == 2  # AAPL on 6/1 and TSLA on 6/2
    assert len(result.horizons) == 3
    assert result.horizons[0].sample_count >= 1
    assert (result.horizons[0].mean_abnormal_ratio or 0.0) > 0
    assert "Effect Sizes" in result.report_text
    assert "Horizon (days)" in result.report_text


def test_f2_study_handles_missing_finra_data(tmp_path: Path):
    db = tmp_path / "flow_f2_empty.db"
    base = load_config().model_dump()
    cfg = AppConfig(**{**base, "state": {"db_path": str(db)}})
    result = run_flow_validation_study(
        finra_csv_dir=tmp_path / "no_finra",
        config=cfg,
        max_horizon_days=2,
    )
    assert result.onset_count == 0
    assert "No FINRA off-exchange rows found." in result.report_text
    assert len(result.horizons) == 2


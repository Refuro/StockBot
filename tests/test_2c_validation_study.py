"""Ticket 2c — validation study mechanics (offline; label + selection logic)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.core.llm import LLMRouter, ModelTier, SpendTracker, StubLLMClient
from stockbot.research.quality_validation import (
    CUTOFF_DATE,
    MIN_HISTORY,
    label_event,
    run_validation_study,
    select_qualifying_events,
)
from stockbot.types import MarketEvent

UTC = timezone.utc


def test_label_is_first_bar_after_event_to_ten_days_later() -> None:
    base = datetime(2026, 3, 2, 21, 0, tzinfo=UTC)
    prices = [(base + timedelta(days=d), 100.0 + d) for d in range(15)]
    event_ts = base + timedelta(hours=12)  # between bar 0 and bar 1
    labeled = label_event(prices, event_ts)
    assert labeled is not None
    entry, exit_, net = labeled
    assert entry == 101.0  # first bar STRICTLY after the event
    assert exit_ == 111.0  # first bar >= entry + 10 days
    assert net == pytest.approx(10 / 101)


def test_label_excludes_events_without_followup_prices() -> None:
    base = datetime(2026, 3, 2, 21, 0, tzinfo=UTC)
    prices = [(base + timedelta(days=d), 100.0) for d in range(3)]  # too short
    assert label_event(prices, base + timedelta(hours=1)) is None


def _event(i: int, ts: datetime, surprise_inputs: tuple[float, float]) -> MarketEvent:
    actual, consensus = surprise_inputs
    return MarketEvent(
        event_id=f"ev-{i}",
        event_type="earnings",
        symbol=f"S{i % 7}",
        as_of=ts,
        payload={"actual_eps": actual, "consensus_eps": consensus},
    )


def test_qualifying_selection_is_post_cutoff_top_decile_on_prior_history() -> None:
    start = datetime(2025, 6, 1, 21, 0, tzinfo=UTC)
    events = []
    # 60 modest pre-cutoff surprises build the history (all +1%..+2%).
    for i in range(60):
        events.append(_event(i, start + timedelta(days=2 * i), (1.01 + 0.0002 * i, 1.0)))
    # Post-cutoff: one huge surprise (qualifies) and one modest (does not).
    events.append(_event(900, CUTOFF_DATE + timedelta(days=5), (1.50, 1.0)))
    events.append(_event(901, CUTOFF_DATE + timedelta(days=6), (1.011, 1.0)))
    # Huge surprise BEFORE the cutoff: passes the decile but not the date.
    events.append(_event(902, CUTOFF_DATE - timedelta(days=30), (1.60, 1.0)))

    qualifying = select_qualifying_events(events)
    ids = [s.event_id for s in qualifying]
    assert "ev-900" in ids
    assert "ev-901" not in ids
    assert "ev-902" not in ids
    assert len(events) > MIN_HISTORY


def test_study_runs_offline_with_stub_router(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    start = datetime(2025, 6, 1, 21, 0, tzinfo=UTC)

    events_rows = []
    for i in range(50):
        events_rows.append(
            {
                "event_id": f"hist-{i}",
                "event_type": "earnings",
                "symbol": "HIST",
                "timestamp": (start + timedelta(days=i)).isoformat(),
                "payload_json": json.dumps({"actual_eps": 1.01, "consensus_eps": 1.0}),
            }
        )
    event_ts = CUTOFF_DATE + timedelta(days=3, hours=21)
    events_rows.append(
        {
            "event_id": "QUAL-earnings-1",
            "event_type": "earnings",
            "symbol": "QUAL",
            "timestamp": event_ts.isoformat(),
            "payload_json": json.dumps(
                {
                    "actual_eps": 1.5,
                    "consensus_eps": 1.0,
                    "cik": 999,
                    "accession_number": "0000000000-26-000123",
                }
            ),
        }
    )
    with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        writer.writeheader()
        writer.writerows(events_rows)

    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume"])
        writer.writeheader()
        for d in range(20):
            writer.writerow(
                {
                    "timestamp": (event_ts - timedelta(hours=1) + timedelta(days=d)).isoformat(),
                    "symbol": "QUAL",
                    "price": f"{100 + 2 * d:.2f}",
                    "bid": "", "ask": "", "volume": 100000,
                }
            )

    # Seed the filing index + doc into the cache so no network is needed.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    from stockbot.data.real_loader import cache_file_path

    index_url = "https://www.sec.gov/Archives/edgar/data/999/000000000026000123/index.json"
    cache_file_path(cache_dir, index_url).write_text(
        json.dumps({"directory": {"item": [{"name": "press8k.htm"}]}}), encoding="utf-8"
    )
    doc_url = "https://www.sec.gov/Archives/edgar/data/999/000000000026000123/press8k.htm"
    cache_file_path(cache_dir, doc_url).with_suffix(".txt").write_text(
        "<html><body>" + ("Revenue grew 30% on broad demand. EPS beat. " * 30) + "</body></html>",
        encoding="utf-8",
    )

    triage = json.dumps({"worth_scoring": True, "reason": "real results"})
    score = json.dumps(
        {
            "quality_score": 0.8,
            "probability_drift_up": 0.7,
            "one_offs": [],
            "guidance_walkback": False,
            "rationale": "durable",
        }
    )
    router = LLMRouter(
        {
            ModelTier.TRIAGE: StubLLMClient("haiku-stub", default=triage),
            ModelTier.STRONG: StubLLMClient("opus-stub", default=score),
            ModelTier.CROSS_CHECK: StubLLMClient("gpt-stub", default=score),
        },
        SpendTracker(cap_usd=25.0, label="test"),
    )

    out = tmp_path / "validation.json"
    summary = run_validation_study(
        data_dir=data_dir, cache_dir=cache_dir, out_path=out, router=router
    )
    assert summary["n_scored"] == 1
    assert summary["n_drifters"] == 1
    assert summary["mean_quality_drifters"] == pytest.approx(0.8)
    assert summary["pre_registered"]["entry_threshold"] == 0.55
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["rows"][0]["label"] == "drifter"

"""Phase 1.5b tests — real-data loader with offline cached fixtures."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.earnings_baseline import BaselineStrategyConfig
from stockbot.config import AppConfig, ExecutionMode, load_config
from stockbot.core.datafeed.base import PointInTimeViolation
from stockbot.core.datafeed.historical import HistoricalReplayFeed
from stockbot.core.state.store import StateStore
from stockbot.data.real_loader import (
    ALPHAVANTAGE_EARNINGS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKERS_URL,
    TIINGO_DAILY_URL,
    cache_file_path,
    ingest_real_dataset,
)
from stockbot.harness.backtest import run_backtest


def _business_days(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed_cached_responses(cache_dir: Path, start: date, end: date) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)

    ticker_map = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp."},
    }
    cache_file_path(cache_dir, SEC_TICKERS_URL).write_text(json.dumps(ticker_map), encoding="utf-8")

    days = _business_days(start, end)
    for ticker, base, cik in (("AAPL", 100.0, 320193), ("MSFT", 200.0, 789019)):
        tiingo_rows = []
        for i, d in enumerate(days):
            px = base + i * 0.04 + (i % 7) * 0.01
            ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()
            tiingo_rows.append(
                {
                    "date": ts,
                    "adjClose": round(px, 4),
                    "close": round(px, 4),
                    "adjVolume": 100000 + i,
                    "volume": 100000 + i,
                }
            )
        params = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "resampleFreq": "daily",
            "token": "dummy-token",
        }
        cache_file_path(cache_dir, TIINGO_DAILY_URL.format(ticker=ticker), params).write_text(
            json.dumps(tiingo_rows), encoding="utf-8"
        )

        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K", "8-K", "10-Q"],
                    "accessionNumber": [
                        f"{cik}-21-000001",
                        f"{cik}-22-000010",
                        f"{cik}-23-000011",
                        f"{cik}-22-000050",
                    ],
                    "filingDate": ["2021-02-01", "2022-08-05", "2023-02-02", "2022-10-25"],
                    "acceptanceDateTime": [
                        "2021-02-01T21:31:00Z",
                        "2022-08-05T21:32:00Z",
                        "2023-02-02T21:32:30Z",
                        "2022-10-25T21:33:00Z",
                    ],
                    "items": ["2.02,9.01", "2.02", "8.01", ""],
                }
            }
        }
        cache_file_path(cache_dir, SEC_SUBMISSIONS_URL.format(cik=cik)).write_text(
            json.dumps(submissions), encoding="utf-8"
        )
        earnings_payload = {
            "symbol": ticker,
            "quarterlyEarnings": [
                {
                    "fiscalDateEnding": "2020-12-31",
                    "reportedDate": "2021-02-01",
                    "reportedEPS": "1.10",
                    "estimatedEPS": "1.00",
                },
                {
                    "fiscalDateEnding": "2022-06-30",
                    "reportedDate": "2022-08-05",
                    "reportedEPS": "0.92",
                    "estimatedEPS": "1.00",
                },
                {
                    "fiscalDateEnding": "2022-09-30",
                    "reportedDate": "2022-11-01",
                    "reportedEPS": "1.15",
                    "estimatedEPS": "1.00",
                },
            ],
        }
        cache_file_path(
            cache_dir,
            ALPHAVANTAGE_EARNINGS_URL,
            {"function": "EARNINGS", "symbol": ticker, "apikey": "dummy-av"},
        ).write_text(json.dumps(earnings_payload), encoding="utf-8")


def _cfg_with_real_csv(tmp_path: Path, prices_path: Path, events_path: Path) -> AppConfig:
    base = load_config().model_dump()
    return AppConfig(
        **{
            **base,
            "mode": ExecutionMode.BACKTEST,
            "state": {"db_path": str(tmp_path / "real_backtest.db")},
            "datafeed": {"backtest": "historical_csv", "paper": "fake_live", "live": "fake_live"},
            "historical_data": {"prices_path": str(prices_path), "events_path": str(events_path)},
        }
    )


def test_offline_real_loader_builds_3y_dataset_and_backtests(tmp_path: Path):
    start = date(2021, 1, 1)
    end = date(2024, 1, 10)
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "real_dataset"
    _seed_cached_responses(cache_dir, start, end)

    paths = ingest_real_dataset(
        tickers=["AAPL", "MSFT"],
        start=start,
        end=end,
        out_dir=out_dir,
        cache_dir=cache_dir,
        tiingo_api_key="dummy-token",
        alphavantage_api_key="dummy-av",
        sec_user_agent="Stockbot-Test/0.1 (test@example.com)",
        offline=True,
        price_provider="tiingo",
        survivorship_biased=True,
    )

    assert paths.prices_path.exists()
    assert paths.events_path.exists()
    meta = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
    assert meta["rows"]["prices"] > 700  # >=3 years of business days for 2 tickers
    assert meta["rows"]["events"] >= 4
    assert meta["survivorship_biased"] is True

    cfg = _cfg_with_real_csv(tmp_path, paths.prices_path, paths.events_path)
    result = run_backtest(
        cfg,
        strategy="earnings_baseline",
        baseline=BaselineStrategyConfig(surprise_threshold=0.05, hold_days=10, quantity=5),
    )
    assert result.steps > 700
    assert result.fills > 0
    assert result.metrics["total_return"] is not None

    store = StateStore(cfg.state.db_path)
    curve = store.get_equity_curve("hello_world", "backtest")
    assert len(curve) == result.snapshots


def test_real_loader_output_respects_point_in_time(tmp_path: Path):
    start = date(2021, 1, 1)
    end = date(2024, 1, 10)
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "real_dataset"
    _seed_cached_responses(cache_dir, start, end)

    paths = ingest_real_dataset(
        tickers=["AAPL"],
        start=start,
        end=end,
        out_dir=out_dir,
        cache_dir=cache_dir,
        tiingo_api_key="dummy-token",
        alphavantage_api_key="dummy-av",
        sec_user_agent="Stockbot-Test/0.1 (test@example.com)",
        offline=True,
    )

    feed = HistoricalReplayFeed.from_csv(paths.prices_path, paths.events_path)
    future = feed.now() + timedelta(days=365)
    with pytest.raises(PointInTimeViolation):
        feed.get_quote_at("AAPL", future)


def test_historical_feed_universe_respects_listing_and_delisting(tmp_path: Path):
    prices = tmp_path / "prices.csv"
    events = tmp_path / "events.csv"
    t0 = datetime(2023, 1, 2, 20, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    t2 = t1 + timedelta(days=1)

    prices.write_text(
        "\n".join(
            [
                "timestamp,symbol,price,bid,ask,volume,listed_at,delisted_at",
                f"{t0.isoformat()},ALIVE,10.0,,,1000,{t0.isoformat()},",
                f"{t1.isoformat()},ALIVE,10.5,,,1000,{t0.isoformat()},",
                f"{t2.isoformat()},ALIVE,11.0,,,1000,{t0.isoformat()},",
                f"{t0.isoformat()},DEAD,20.0,,,1000,{t0.isoformat()},{t1.isoformat()}",
                f"{t1.isoformat()},DEAD,19.5,,,1000,{t0.isoformat()},{t1.isoformat()}",
            ]
        ),
        encoding="utf-8",
    )
    events.write_text("event_id,event_type,symbol,timestamp,payload_json\n", encoding="utf-8")

    feed = HistoricalReplayFeed.from_csv(prices, events)
    assert set(feed.get_universe()) == {"ALIVE", "DEAD"}

    feed.advance()
    assert set(feed.get_universe()) == {"ALIVE", "DEAD"}

    feed.advance()
    assert set(feed.get_universe()) == {"ALIVE"}


def test_real_loader_uses_item_202_and_computes_real_surprises(tmp_path: Path):
    start = date(2021, 1, 1)
    end = date(2024, 1, 10)
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "real_dataset"
    _seed_cached_responses(cache_dir, start, end)

    paths = ingest_real_dataset(
        tickers=["AAPL", "MSFT"],
        start=start,
        end=end,
        out_dir=out_dir,
        cache_dir=cache_dir,
        tiingo_api_key="dummy-token",
        alphavantage_api_key="dummy-av",
        sec_user_agent="Stockbot-Test/0.1 (test@example.com)",
        offline=True,
    )
    rows = list(csv.DictReader(io.StringIO(paths.events_path.read_text(encoding="utf-8"))))
    assert rows
    # 4 item-2.02 earnings events (2 per ticker).
    assert len(rows) == 4
    payloads = []
    for row in rows:
        assert row["event_type"] == "earnings"
        payload = json.loads(row["payload_json"])
        assert "2.02" in str(payload.get("items", ""))
        payloads.append(payload)

    surprises = [float(p["surprise_pct"]) for p in payloads]
    assert len(set(round(s, 6) for s in surprises)) >= 2
    # Hand-checked: (1.10-1.00)/1.00 = +0.10, (0.92-1.00)/1.00 = -0.08.
    assert 0.10 in [round(s, 2) for s in surprises]
    assert -0.08 in [round(s, 2) for s in surprises]

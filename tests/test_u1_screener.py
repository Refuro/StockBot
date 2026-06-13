"""Ticket U1 — mechanical universe screener (offline fixtures)."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from stockbot.data.real_loader import TIINGO_DAILY_URL, cache_file_path
from stockbot.data.screen_universe import (
    AV_QUERY_URL,
    market_cap_in_band,
    median_dollar_volume,
    overview_payload_valid,
    primary_exchange_stocks,
    screen_universe,
)
from stockbot.data.universe import load_universe


def test_overview_payload_validation() -> None:
    assert overview_payload_valid({"Symbol": "ABC", "MarketCapitalization": "5"}) is True
    assert overview_payload_valid({}) is True  # documented "no data" answer is cacheable
    assert overview_payload_valid({"Note": "rate limited"}) is False  # throttle never cached
    assert overview_payload_valid({"Information": "premium"}) is False
    assert overview_payload_valid("nope") is False


def test_primary_exchange_stock_filter() -> None:
    rows = [
        {"symbol": "GOOD", "exchange": "NYSE", "assetType": "Stock"},
        {"symbol": "NASD", "exchange": "NASDAQ", "assetType": "Stock"},
        {"symbol": "ETFX", "exchange": "NYSE", "assetType": "ETF"},
        {"symbol": "OTCX", "exchange": "OTC", "assetType": "Stock"},
        {"symbol": "WS-U", "exchange": "NYSE", "assetType": "Stock"},  # unit/warrant style
        {"symbol": "BRK.B", "exchange": "NYSE", "assetType": "Stock"},  # share class
    ]
    kept = {r["symbol"] for r in primary_exchange_stocks(rows)}
    assert kept == {"GOOD", "NASD"}


def test_market_cap_band() -> None:
    assert market_cap_in_band({"MarketCapitalization": "500000000"}) is True
    assert market_cap_in_band({"MarketCapitalization": "200000000"}) is False  # too small
    assert market_cap_in_band({"MarketCapitalization": "5000000000"}) is False  # too big
    assert market_cap_in_band({"MarketCapitalization": "None"}) is False
    assert market_cap_in_band({}) is False


def test_median_dollar_volume() -> None:
    rows = [
        {"price": 10.0, "volume": 100_000},
        {"price": 10.0, "volume": 50_000},
        {"price": 10.0, "volume": 200_000},
        {"price": 10.0, "volume": 0},  # halted day: ignored
    ]
    assert median_dollar_volume(rows) == 10.0 * 100_000


def _seed_screener_cache(cache_dir: Path, api_key: str, tiingo_key: str) -> None:
    listing_csv = (
        "symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
        "INBND,In Band Corp,NYSE,Stock,2010-01-01,null,Active\n"
        "THIN,Thin Volume Inc,NASDAQ,Stock,2012-01-01,null,Active\n"
        "BIGCO,Mega Cap Inc,NYSE,Stock,2005-01-01,null,Active\n"
        "FUNDX,Some Fund,NYSE,ETF,2015-01-01,null,Active\n"
    )
    params = {"function": "LISTING_STATUS", "state": "active", "apikey": api_key}
    cache_file_path(cache_dir, AV_QUERY_URL, params).with_suffix(".txt").write_text(
        listing_csv, encoding="utf-8"
    )

    overviews = {
        "INBND": {
            "Symbol": "INBND", "AssetType": "Common Stock", "Country": "USA",
            "Sector": "TECHNOLOGY", "MarketCapitalization": "800000000",
        },
        "THIN": {
            "Symbol": "THIN", "AssetType": "Common Stock", "Country": "USA",
            "Sector": "ENERGY", "MarketCapitalization": "400000000",
        },
        "BIGCO": {
            "Symbol": "BIGCO", "AssetType": "Common Stock", "Country": "USA",
            "Sector": "FINANCE", "MarketCapitalization": "90000000000",
        },
    }
    for symbol, payload in overviews.items():
        p = {"function": "OVERVIEW", "symbol": symbol, "apikey": api_key}
        cache_file_path(cache_dir, AV_QUERY_URL, p).write_text(json.dumps(payload), encoding="utf-8")

    def tiingo_rows(volume: int) -> list[dict]:
        return [
            {"date": f"2026-01-{d:02d}T00:00:00.000Z", "adjClose": 20.0, "adjVolume": volume}
            for d in range(2, 12)
        ]

    for symbol, volume in (("INBND", 100_000), ("THIN", 1_000)):  # $2M vs $20k median
        p = {
            "startDate": "2026-01-01",
            "endDate": "2026-02-01",
            "resampleFreq": "daily",
            "token": tiingo_key,
        }
        cache_file_path(cache_dir, TIINGO_DAILY_URL.format(ticker=symbol), p).write_text(
            json.dumps(tiingo_rows(volume)), encoding="utf-8"
        )


def test_screen_universe_offline_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-av-key")
    monkeypatch.setenv("TIINGO_API_KEY", "test-tiingo-key")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _seed_screener_cache(cache_dir, "test-av-key", "test-tiingo-key")
    out = tmp_path / "universe_expanded.csv"

    metadata = screen_universe(
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        cache_dir=cache_dir,
        out_path=out,
        av_rps=10.0,
        tiingo_sleep_s=0.0,
        offline=True,
    )

    # FUNDX filtered (ETF); BIGCO out of band; THIN fails the ADV floor.
    assert metadata["counts"]["primary_exchange_stocks"] == 3
    assert metadata["counts"]["cap_band"] == 2
    assert metadata["counts"]["final_universe"] == 1
    with out.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [{"symbol": "INBND", "sector": "Technology"}]

    # Output is consumable by the standard universe loader (ingest + study path).
    entries = load_universe(out)
    assert [e.symbol for e in entries] == ["INBND"]

    meta_path = out.with_suffix(".metadata.json")
    assert meta_path.exists()
    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved["screen"]["market_cap_band_usd"] == [300e6, 3e9]
    assert any("point-in-time" in c for c in saved["caveats"])

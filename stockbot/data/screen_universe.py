"""Ticket U1 — mechanical universe screener (no hand-curation).

Screen: every active US-listed common stock on a primary exchange (NYSE /
NASDAQ, from Alpha Vantage LISTING_STATUS), with market cap in the
$300M–$3B band (AV OVERVIEW, measured at screen time) and median daily
dollar volume >= $500k (computed from Tiingo prices over the study window).

Outputs `stockbot/data/universe_expanded.csv` (symbol,sector) plus a metadata
JSON recording the screen date, parameters, and stage-by-stage counts so the
screen is reproducible. Resumable: everything goes through `CachedHTTP`.

Honesty caveats (recorded in metadata, repeated in the drift report):
- Market cap is measured at screen time, not point-in-time — names that were
  in-band historically but migrated out are missed, and vice versa.
- Survivorship: names delisted before the screen date never appear.

Run:
    python -m stockbot.data.screen_universe --start 2022-06-01 --end 2026-06-09
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from stockbot.config import load_env
from stockbot.data.real_loader import (
    ALPHAVANTAGE_EARNINGS_URL,
    TIINGO_DAILY_URL,
    CachedHTTP,
    CacheValidationError,
    alpha_vantage_payload_is_throttle,
    load_tiingo_prices,
)

AV_QUERY_URL = ALPHAVANTAGE_EARNINGS_URL  # same base endpoint, different `function`

DEFAULT_OUT = Path(__file__).parent / "universe_expanded.csv"

MIN_MARKET_CAP = 300e6
MAX_MARKET_CAP = 3e9
MIN_MEDIAN_DOLLAR_VOLUME = 500_000.0
PRIMARY_EXCHANGES = {"NYSE", "NASDAQ"}

_PLAIN_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def overview_payload_valid(data: Any) -> bool:
    """Valid = a real overview dict OR the documented 'no data' empty dict.

    Throttle notes ({"Note"/"Information": ...}) are invalid and must never be
    cached as an answer.
    """
    if not isinstance(data, dict):
        return False
    if alpha_vantage_payload_is_throttle(data):
        return False
    return data == {} or "Symbol" in data


def listing_status_rows(client: CachedHTTP, api_key: str) -> list[dict[str, str]]:
    """Active listings from AV LISTING_STATUS (CSV endpoint)."""
    text = client.get_text(
        AV_QUERY_URL,
        params={"function": "LISTING_STATUS", "state": "active", "apikey": api_key},
    )
    return list(csv.DictReader(io.StringIO(text)))


def primary_exchange_stocks(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Common stocks on NYSE/NASDAQ with plain tickers (no units/warrants/classes)."""
    out = []
    for row in rows:
        if str(row.get("assetType", "")).strip() != "Stock":
            continue
        if str(row.get("exchange", "")).strip() not in PRIMARY_EXCHANGES:
            continue
        symbol = str(row.get("symbol", "")).strip().upper()
        if not _PLAIN_TICKER_RE.match(symbol):
            continue
        out.append({**row, "symbol": symbol})
    return out


def fetch_overview(client: CachedHTTP, symbol: str, api_key: str) -> dict[str, Any]:
    return client.get_json(
        AV_QUERY_URL,
        params={"function": "OVERVIEW", "symbol": symbol, "apikey": api_key},
        validate=overview_payload_valid,
    )


def market_cap_in_band(overview: dict[str, Any]) -> bool:
    raw = overview.get("MarketCapitalization")
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return False
    return MIN_MARKET_CAP <= cap <= MAX_MARKET_CAP


def is_us_common(overview: dict[str, Any]) -> bool:
    if not overview or "Symbol" not in overview:
        return False
    if str(overview.get("Country", "")).strip() not in {"USA", "United States"}:
        return False
    if str(overview.get("AssetType", "")).strip() not in {"Common Stock", "Stock"}:
        return False
    return True


def median_dollar_volume(rows: list[dict[str, Any]]) -> float:
    values = [float(r["price"]) * float(r["volume"]) for r in rows if float(r.get("volume", 0)) > 0]
    return float(statistics.median(values)) if values else 0.0


def _retry_av(fn, *, what: str, attempts: int = 5, backoff_s: float = 20.0):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except CacheValidationError as exc:
            if not alpha_vantage_payload_is_throttle(exc.payload) or attempt == attempts:
                raise
            wait = backoff_s * attempt
            print(f"  {what}: AV throttled, retrying in {wait:.0f}s ({attempt}/{attempts})")
            time.sleep(wait)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {429, 500, 502, 503} or attempt == attempts:
                raise
            time.sleep(backoff_s * attempt)


def screen_universe(
    *,
    start: date,
    end: date,
    cache_dir: Path,
    out_path: Path,
    av_rps: float,
    tiingo_sleep_s: float,
    offline: bool = False,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    env = load_env()
    client = CachedHTTP(cache_dir=cache_dir, offline=offline)

    listings = listing_status_rows(client, env.alphavantage_api_key)
    candidates = primary_exchange_stocks(listings)
    if max_symbols is not None:
        candidates = candidates[:max_symbols]
    print(f"Listings: {len(listings)} active; primary-exchange common stocks: {len(candidates)}")

    in_band: list[dict[str, Any]] = []
    overview_no_data = 0
    overview_errors = 0
    for i, row in enumerate(candidates):
        symbol = row["symbol"]
        if i and i % 250 == 0:
            print(f"  OVERVIEW progress: {i}/{len(candidates)} (in-band so far: {len(in_band)})")
        already_cached = client.has_cached(
            AV_QUERY_URL,
            params={"function": "OVERVIEW", "symbol": symbol, "apikey": env.alphavantage_api_key},
            validate=overview_payload_valid,
        )
        try:
            overview = _retry_av(
                lambda: fetch_overview(client, symbol, env.alphavantage_api_key),
                what=f"{symbol} overview",
            )
        except Exception as exc:  # noqa: BLE001 - per-symbol isolation
            overview_errors += 1
            print(f"  {symbol}: overview failed: {exc}")
            continue
        if not already_cached and not offline:
            time.sleep(1.0 / av_rps)
        if not overview:
            overview_no_data += 1
            continue
        if is_us_common(overview) and market_cap_in_band(overview):
            in_band.append(
                {
                    "symbol": symbol,
                    "sector": str(overview.get("Sector", "")).strip().title(),
                    "market_cap": float(overview["MarketCapitalization"]),
                    "exchange": row.get("exchange", ""),
                }
            )
    print(f"Cap band ${MIN_MARKET_CAP/1e6:.0f}M-${MAX_MARKET_CAP/1e9:.0f}B: {len(in_band)} names")

    final: list[dict[str, Any]] = []
    adv_excluded = 0
    price_errors = 0
    for i, entry in enumerate(in_band):
        symbol = entry["symbol"]
        if i and i % 100 == 0:
            print(f"  ADV progress: {i}/{len(in_band)} (passing so far: {len(final)})")
        already_cached = client.has_cached(
            TIINGO_DAILY_URL.format(ticker=symbol),
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "resampleFreq": "daily",
                "token": env.tiingo_api_key,
            },
        )
        try:
            rows = load_tiingo_prices(client, symbol, start, end, api_key=env.tiingo_api_key)
        except Exception as exc:  # noqa: BLE001 - per-symbol isolation
            price_errors += 1
            print(f"  {symbol}: tiingo failed: {exc}")
            continue
        if not already_cached and not offline:
            time.sleep(tiingo_sleep_s)
        mdv = median_dollar_volume(rows)
        if mdv >= MIN_MEDIAN_DOLLAR_VOLUME:
            final.append({**entry, "median_dollar_volume": mdv})
        else:
            adv_excluded += 1

    final.sort(key=lambda e: e["symbol"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "sector"])
        writer.writeheader()
        for entry in final:
            writer.writerow({"symbol": entry["symbol"], "sector": entry["sector"]})

    metadata = {
        "screened_at": datetime.now(timezone.utc).isoformat(),
        "screen": {
            "exchanges": sorted(PRIMARY_EXCHANGES),
            "asset_type": "Stock (common)",
            "market_cap_band_usd": [MIN_MARKET_CAP, MAX_MARKET_CAP],
            "cap_measured": "at screen time via AV OVERVIEW (NOT point-in-time)",
            "min_median_dollar_volume": MIN_MEDIAN_DOLLAR_VOLUME,
            "adv_window": [start.isoformat(), end.isoformat()],
            "ticker_pattern": _PLAIN_TICKER_RE.pattern,
        },
        "counts": {
            "active_listings": len(listings),
            "primary_exchange_stocks": len(candidates),
            "overview_no_data": overview_no_data,
            "overview_errors": overview_errors,
            "cap_band": len(in_band),
            "adv_excluded": adv_excluded,
            "price_errors": price_errors,
            "final_universe": len(final),
        },
        "caveats": [
            "Market-cap band judged at screen time, not point-in-time.",
            "Survivorship: names delisted before the screen date are absent.",
        ],
        "symbols": [
            {
                "symbol": e["symbol"],
                "sector": e["sector"],
                "market_cap": e["market_cap"],
                "median_dollar_volume": round(e["median_dollar_volume"], 2),
                "exchange": e["exchange"],
            }
            for e in final
        ],
    }
    meta_path = out_path.with_suffix(".metadata.json")
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Universe written: {out_path} ({len(final)} names); metadata: {meta_path}")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="U1 mechanical universe screener")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (ADV window start)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (ADV window end)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--cache-dir", default="fixtures/real_cache")
    parser.add_argument("--av-rps", type=float, default=1.0, help="AV requests/second")
    parser.add_argument("--tiingo-sleep", type=float, default=0.15)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None, help="Debug: cap candidate count")
    args = parser.parse_args(argv)

    screen_universe(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache_dir=Path(args.cache_dir),
        out_path=Path(args.out),
        av_rps=args.av_rps,
        tiingo_sleep_s=args.tiingo_sleep,
        offline=args.offline,
        max_symbols=args.max_symbols,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

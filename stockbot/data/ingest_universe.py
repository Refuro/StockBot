"""Resumable universe ingestion for shot 1 (Ticket 2b data path).

Fills the on-disk HTTP cache for the candidate universe, respecting the Alpha
Vantage free-tier budget (25 requests/day), then builds the replay dataset from
whichever tickers are fully cached. Designed to be re-run daily: cached tickers
cost nothing, so coverage expands by ~25 names per day until complete.

Usage (kick off day one, re-run until coverage is full):
    python -m stockbot.data.ingest_universe --start 2023-06-01 --end 2026-06-09
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from stockbot.config import load_env
from stockbot.data.real_loader import (
    ALPHAVANTAGE_EARNINGS_URL,
    CachedHTTP,
    CacheValidationError,
    SEC_SUBMISSIONS_URL,
    TIINGO_DAILY_URL,
    alpha_vantage_earnings_params,
    alpha_vantage_earnings_payload_valid,
    alpha_vantage_payload_is_throttle,
    ingest_real_dataset,
    load_alpha_vantage_earnings,
    load_sec_ticker_map,
    load_tiingo_prices,
)
from stockbot.data.universe import DEFAULT_UNIVERSE_PATH, load_universe

RETRYABLE_STATUS = {429, 500, 502, 503}


def _tiingo_params(ticker: str, start: date, end: date, api_key: str) -> dict[str, str]:
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "resampleFreq": "daily",
        "token": api_key,
    }


def _with_retries(fn, *, what: str, attempts: int = 4, backoff_s: float = 30.0):
    """Run fn(); on retryable HTTP errors back off and retry. Raises on final failure."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in RETRYABLE_STATUS or attempt == attempts:
                raise
            wait = backoff_s * attempt
            print(f"  {what}: HTTP {status}, retrying in {wait:.0f}s ({attempt}/{attempts})")
            time.sleep(wait)


def fetch_universe_cache(
    *,
    tickers: list[str],
    start: date,
    end: date,
    cache_dir: Path,
    av_budget: int,
    offline: bool,
    av_sleep_s: float = 1.0,
) -> dict[str, dict[str, Any]]:
    """Ensure per-ticker cache entries exist; returns per-ticker status map."""
    env = load_env()
    client = CachedHTTP(cache_dir=cache_dir, offline=offline)
    status: dict[str, dict[str, Any]] = {}

    ticker_map: dict[str, int] = {}
    try:
        ticker_map = load_sec_ticker_map(client, env.sec_user_agent)
    except Exception as exc:  # noqa: BLE001 - report and continue offline
        print(f"SEC ticker map unavailable: {exc}")

    # Tombstones: tickers Alpha Vantage has confirmed it has no data for (e.g.
    # delisted). Persisted so daily re-runs never re-spend budget on them.
    no_data_path = cache_dir / "av_no_data.json"
    av_no_data: set[str] = set()
    if no_data_path.exists():
        av_no_data = set(json.loads(no_data_path.read_text(encoding="utf-8")))

    av_spent = 0
    av_throttled = False
    for ticker in tickers:
        entry: dict[str, Any] = {"prices": False, "sec": False, "earnings": False, "error": None}
        status[ticker] = entry
        cik = ticker_map.get(ticker)
        if cik is None:
            entry["error"] = "no CIK mapping"

        # Prices (Tiingo) — free-tier friendly, retried on 429.
        try:
            tiingo_url = TIINGO_DAILY_URL.format(ticker=ticker)
            tiingo_params = _tiingo_params(ticker, start, end, env.tiingo_api_key)
            cached = client.has_cached(tiingo_url, params=tiingo_params)
            if not cached and offline:
                raise FileNotFoundError("offline cache miss")
            rows = _with_retries(
                lambda: load_tiingo_prices(client, ticker, start, end, api_key=env.tiingo_api_key),
                what=f"{ticker} tiingo",
            )
            entry["prices"] = bool(rows)
            if not cached and not offline:
                time.sleep(0.3)  # stay friendly to the rate limiter
        except Exception as exc:  # noqa: BLE001 - per-ticker isolation
            entry["error"] = f"tiingo: {exc}"

        # SEC submissions (free).
        if cik is not None:
            try:
                sec_url = SEC_SUBMISSIONS_URL.format(cik=cik)
                cached = client.has_cached(sec_url)
                if not cached and offline:
                    raise FileNotFoundError("offline cache miss")
                _with_retries(
                    lambda: client.get_json(sec_url, headers={"User-Agent": env.sec_user_agent}),
                    what=f"{ticker} sec",
                )
                entry["sec"] = True
                if not cached and not offline:
                    time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"sec: {exc}"

        # Alpha Vantage earnings — budgeted at 25 network requests/day. No CIK
        # means no SEC filings to join against, so don't spend budget there.
        av_params = alpha_vantage_earnings_params(ticker, env.alphavantage_api_key)
        if client.has_cached(
            ALPHAVANTAGE_EARNINGS_URL, params=av_params, validate=alpha_vantage_earnings_payload_valid
        ):
            entry["earnings"] = True
        elif cik is None or ticker in av_no_data:
            if ticker in av_no_data:
                entry["error"] = entry["error"] or "alphavantage: no data (tombstoned)"
        elif offline or av_throttled or av_spent >= av_budget:
            pass  # leave for the next daily run
        else:
            # Premium keys throttle per-minute, not per-day: retry a few times
            # before declaring the run throttled (free-tier daily cap behaviour).
            for attempt in range(1, 6):
                try:
                    load_alpha_vantage_earnings(client, ticker, env.alphavantage_api_key)
                    entry["earnings"] = True
                    av_spent += 1
                    time.sleep(av_sleep_s)
                    break
                except CacheValidationError as exc:
                    if alpha_vantage_payload_is_throttle(exc.payload):
                        if attempt < 5:
                            print(f"  {ticker} earnings: AV throttled, retrying in 20s ({attempt}/5)")
                            time.sleep(20.0)
                            continue
                        av_throttled = True
                        print(f"  Alpha Vantage throttled at {ticker} (budget spent: {av_spent}): {exc}")
                    else:
                        av_spent += 1
                        av_no_data.add(ticker)
                        entry["error"] = "alphavantage: no data for symbol"
                    break
                except Exception as exc:  # noqa: BLE001
                    entry["error"] = f"alphavantage: {exc}"
                    av_spent += 1  # failed call still consumed a request
                    break

    no_data_path.write_text(json.dumps(sorted(av_no_data)), encoding="utf-8")
    print(f"Alpha Vantage requests spent this run: {av_spent} (budget {av_budget})")
    return status


def ready_tickers(status: dict[str, dict[str, Any]]) -> list[str]:
    """Tickers with everything cached: prices + SEC submissions + valid earnings."""
    return [t for t, s in status.items() if s["prices"] and s["sec"] and s["earnings"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resumable shot-1 universe ingestion")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out-dir", default="data/real")
    parser.add_argument("--cache-dir", default="fixtures/real_cache")
    parser.add_argument("--av-budget", type=int, default=25, help="Alpha Vantage requests this run")
    parser.add_argument("--offline", action="store_true", help="Cache only; no network")
    parser.add_argument("--skip-build", action="store_true", help="Fetch cache only, don't build CSVs")
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    tickers = [e.symbol for e in load_universe(args.universe)]
    cache_dir = Path(args.cache_dir)
    out_dir = Path(args.out_dir)

    print(f"Universe: {len(tickers)} candidates; cache: {cache_dir}")
    status = fetch_universe_cache(
        tickers=tickers,
        start=start,
        end=end,
        cache_dir=cache_dir,
        av_budget=args.av_budget,
        offline=args.offline,
    )
    ready = ready_tickers(status)
    pending = [t for t in tickers if t not in ready]
    print(f"Fully cached: {len(ready)}/{len(tickers)}; pending: {len(pending)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    coverage = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "universe_size": len(tickers),
        "ready": sorted(ready),
        "pending": sorted(pending),
        "status": status,
    }
    coverage_path = out_dir / "coverage.json"
    coverage_path.write_text(json.dumps(coverage, indent=2, default=str), encoding="utf-8")
    print(f"Coverage written: {coverage_path}")

    if args.skip_build or not ready:
        if not ready:
            print("No fully-cached tickers yet; skipping dataset build.")
        return 0

    env = load_env()
    paths = ingest_real_dataset(
        tickers=sorted(ready),
        start=start,
        end=end,
        out_dir=out_dir,
        cache_dir=cache_dir,
        tiingo_api_key=env.tiingo_api_key,
        sec_user_agent=env.sec_user_agent,
        alphavantage_api_key=env.alphavantage_api_key,
        offline=True,  # everything 'ready' is cached; never hit network during build
        survivorship_biased=True,
    )
    print(f"Dataset built from {len(ready)} tickers:")
    print(f"  prices:   {paths.prices_path}")
    print(f"  events:   {paths.events_path}")
    print(f"  metadata: {paths.metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

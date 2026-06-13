"""CLI entrypoint to pull real historical data into feed CSVs (Phase 1.5b)."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from stockbot.config import load_env
from stockbot.data.real_loader import ingest_real_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest real historical prices + SEC filing events")
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers, e.g. AAPL,MSFT,AMD")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--out-dir", default="data/real", help="Output directory for prices/events CSVs")
    parser.add_argument("--cache-dir", default="fixtures/real_cache", help="HTTP cache directory")
    parser.add_argument("--offline", action="store_true", help="Only use cached responses, no network")
    parser.add_argument(
        "--price-provider",
        default="tiingo",
        choices=["tiingo"],
        help="Price source (tiingo for now)",
    )
    parser.add_argument(
        "--include-delisted",
        action="store_true",
        help="Mark output universe as including delisted symbols (disables survivorship-bias flag)",
    )
    args = parser.parse_args(argv)

    env = load_env()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if not tickers:
        raise ValueError("Provide at least one ticker via --tickers")
    if end < start:
        raise ValueError("--end must be >= --start")

    paths = ingest_real_dataset(
        tickers=tickers,
        start=start,
        end=end,
        out_dir=Path(args.out_dir),
        cache_dir=Path(args.cache_dir),
        tiingo_api_key=env.tiingo_api_key,
        alphavantage_api_key=env.alphavantage_api_key,
        sec_user_agent=env.sec_user_agent,
        offline=args.offline,
        price_provider=args.price_provider,
        survivorship_biased=not args.include_delisted,
    )
    print(f"Wrote prices:   {paths.prices_path}")
    print(f"Wrote events:   {paths.events_path}")
    print(f"Wrote metadata: {paths.metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


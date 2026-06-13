"""Real historical data loader with cache + offline fixture mode (Phase 1.5b).

Pulls:
- Daily prices/volume from Tiingo.
- Real 8-K earnings-release filing events from SEC submissions (Item 2.02).
- Actual vs consensus EPS from Alpha Vantage earnings estimates.

Writes outputs in the CSV format consumed by `HistoricalReplayFeed`:
- prices.csv: timestamp,symbol,price,bid,ask,volume,listed_at,delisted_at
- events.csv: event_id,event_type,symbol,timestamp,payload_json
"""

from __future__ import annotations

import csv
import hashlib
import math
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from stockbot.bots.proportionality.surprise import standardized_surprise

TIINGO_DAILY_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ALPHAVANTAGE_EARNINGS_URL = "https://www.alphavantage.co/query"


@dataclass(frozen=True)
class DatasetPaths:
    prices_path: Path
    events_path: Path
    metadata_path: Path


def cache_key(url: str, params: dict[str, Any] | None = None) -> str:
    query = urlencode(sorted((params or {}).items()), doseq=True)
    base = f"{url}?{query}" if query else url
    return hashlib.sha256(base.encode()).hexdigest()


def cache_file_path(cache_dir: str | Path, url: str, params: dict[str, Any] | None = None) -> Path:
    return Path(cache_dir) / f"{cache_key(url, params)}.json"


class CacheValidationError(RuntimeError):
    """A fresh API response failed validation and was NOT cached (e.g. rate-limit note)."""

    def __init__(self, message: str, payload: Any = None) -> None:
        super().__init__(message)
        self.payload = payload


class CachedHTTP:
    """HTTP client with deterministic on-disk cache and optional offline mode."""

    def __init__(self, cache_dir: str | Path, offline: bool = False, timeout_s: float = 30.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.timeout_s = timeout_s

    def has_cached(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        validate: Callable[[Any], bool] | None = None,
    ) -> bool:
        """True if a (valid) cached response exists for this request."""
        cache_path = cache_file_path(self.cache_dir, url, params)
        if not cache_path.exists():
            return False
        if validate is None:
            return True
        try:
            return bool(validate(json.loads(cache_path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, OSError):
            return False

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        validate: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Fetch JSON through the disk cache.

        ``validate`` guards against cache poisoning by HTTP-200 error bodies
        (Alpha Vantage returns rate-limit notes with status 200): an invalid
        fresh response raises `CacheValidationError` and is never written to
        cache; an invalid *cached* response is deleted and refetched.
        """
        cache_path = cache_file_path(self.cache_dir, url, params)
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if validate is None or validate(data):
                return data
            cache_path.unlink()  # poisoned entry: heal by refetching below
        if self.offline:
            raise FileNotFoundError(f"Offline mode: cache miss for {url} params={params}")
        with httpx.Client(timeout=self.timeout_s) as client:
            resp = client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if validate is not None and not validate(data):
            raise CacheValidationError(
                f"Response failed validation (not cached) for {url}: {str(data)[:200]}",
                payload=data,
            )
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Fetch a text/HTML document through the disk cache (e.g. SEC filings)."""
        cache_path = cache_file_path(self.cache_dir, url, params).with_suffix(".txt")
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
        if self.offline:
            raise FileNotFoundError(f"Offline mode: cache miss for {url} params={params}")
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            text = resp.text
        cache_path.write_text(text, encoding="utf-8")
        return text


def _iso_to_utc(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_tiingo_prices(
    client: CachedHTTP,
    ticker: str,
    start: date,
    end: date,
    api_key: str,
) -> list[dict[str, Any]]:
    if not api_key:
        raise ValueError("TIINGO_API_KEY is required for Tiingo ingestion")
    url = TIINGO_DAILY_URL.format(ticker=ticker)
    params = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "resampleFreq": "daily",
        "token": api_key,
    }
    rows = client.get_json(url, params=params)
    out: list[dict[str, Any]] = []
    for row in rows:
        ts = _iso_to_utc(row["date"]).replace(hour=20, minute=0, second=0, microsecond=0)
        price = float(row.get("adjClose", row.get("close", 0.0)))
        volume = int(float(row.get("adjVolume", row.get("volume", 0.0))))
        out.append(
            {
                "timestamp": ts.isoformat(),
                "symbol": ticker.upper(),
                "price": price,
                "bid": "",
                "ask": "",
                "volume": volume,
                "listed_at": "",
                "delisted_at": "",
            }
        )
    return out


def load_sec_ticker_map(client: CachedHTTP, sec_user_agent: str) -> dict[str, int]:
    raw = client.get_json(SEC_TICKERS_URL, headers={"User-Agent": sec_user_agent})
    mapping: dict[str, int] = {}
    for _, row in raw.items():
        ticker = str(row["ticker"]).upper()
        mapping[ticker] = int(row["cik_str"])
    return mapping


def _safe_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def _safe_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def alpha_vantage_earnings_payload_valid(data: Any) -> bool:
    """True only for genuine earnings payloads — rejects HTTP-200 throttle notes."""
    return isinstance(data, dict) and isinstance(data.get("quarterlyEarnings"), list)


def alpha_vantage_payload_is_throttle(data: Any) -> bool:
    """True for HTTP-200 rate-limit bodies ({"Note": ...} / {"Information": ...}).

    An empty dict means "no data for this symbol" (e.g. delisted), NOT a throttle.
    """
    return isinstance(data, dict) and any(k in data for k in ("Note", "Information", "Error Message"))


def alpha_vantage_earnings_params(ticker: str, api_key: str) -> dict[str, str]:
    return {"function": "EARNINGS", "symbol": ticker.upper(), "apikey": api_key}


def load_alpha_vantage_earnings(
    client: CachedHTTP,
    ticker: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Load quarterly actual/consensus EPS pairs from Alpha Vantage."""
    if not api_key:
        raise ValueError("ALPHAVANTAGE_API_KEY is required for earnings surprise ingestion")
    payload = client.get_json(
        ALPHAVANTAGE_EARNINGS_URL,
        params=alpha_vantage_earnings_params(ticker, api_key),
        validate=alpha_vantage_earnings_payload_valid,
    )
    rows = payload.get("quarterlyEarnings", [])
    out: list[dict[str, Any]] = []
    for row in rows:
        actual = _safe_float(row.get("reportedEPS"))
        consensus = _safe_float(row.get("estimatedEPS"))
        reported_date = _safe_date(row.get("reportedDate"))
        fiscal_end = _safe_date(row.get("fiscalDateEnding"))
        if actual is None or consensus is None or reported_date is None:
            continue
        surprise = standardized_surprise(actual, consensus)
        if surprise is None:
            continue
        release_ts = datetime(
            reported_date.year,
            reported_date.month,
            reported_date.day,
            21,
            30,
            tzinfo=timezone.utc,
        )
        out.append(
            {
                "reported_date": reported_date,
                "fiscal_period_end": fiscal_end.isoformat() if fiscal_end else None,
                "actual_eps": actual,
                "consensus_eps": consensus,
                "surprise_pct": surprise,
                # PIT contract: consensus must exist before release.
                "consensus_as_of": (release_ts - timedelta(seconds=1)).isoformat(),
            }
        )
    out.sort(key=lambda r: r["reported_date"])
    return out


def _is_item_202(items_raw: Any) -> bool:
    if not items_raw:
        return False
    if not isinstance(items_raw, str):
        items_raw = str(items_raw)
    tokens = [tok.strip() for tok in items_raw.replace(";", ",").split(",") if tok.strip()]
    return any(tok.startswith("2.02") for tok in tokens)


def _match_earnings_print(earnings: list[dict[str, Any]], filing_date: date) -> dict[str, Any] | None:
    if not earnings:
        return None
    best = min(earnings, key=lambda r: abs((r["reported_date"] - filing_date).days))
    if abs((best["reported_date"] - filing_date).days) > 10:
        return None
    return best


def load_sec_8k_events(
    client: CachedHTTP,
    ticker: str,
    cik: int,
    start: date,
    end: date,
    sec_user_agent: str,
    earnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    url = SEC_SUBMISSIONS_URL.format(cik=cik)
    raw = client.get_json(url, headers={"User-Agent": sec_user_agent})
    recent = raw.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    acceptance = recent.get("acceptanceDateTime", [])
    items = recent.get("items", [])

    out: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if not str(form).startswith("8-K"):
            continue
        if not _is_item_202(items[i] if i < len(items) else ""):
            continue
        fd = date.fromisoformat(filing_dates[i])
        if fd < start or fd > end:
            continue
        earnings_row = _match_earnings_print(earnings, fd)
        if earnings_row is None:
            continue
        acc_raw = acceptance[i] if i < len(acceptance) else ""
        if acc_raw:
            ts = _iso_to_utc(str(acc_raw).replace(" ", "T"))
        else:
            ts = datetime(fd.year, fd.month, fd.day, 21, 30, tzinfo=timezone.utc)
        acc_no = accession[i]
        out.append(
            {
                "event_id": f"{ticker}-earnings-{acc_no}",
                "event_type": "earnings",
                "symbol": ticker,
                "timestamp": ts.isoformat(),
                "payload_json": json.dumps(
                    {
                        "form": form,
                        "items": items[i] if i < len(items) else "",
                        "source": "sec_submissions_8k",
                        "surprise_pct": earnings_row["surprise_pct"],
                        "actual_eps": earnings_row["actual_eps"],
                        "consensus_eps": earnings_row["consensus_eps"],
                        "consensus_as_of": earnings_row["consensus_as_of"],
                        "fiscal_period_end": earnings_row["fiscal_period_end"],
                        "accession_number": acc_no,
                        "filing_date": filing_dates[i],
                        "acceptance_datetime": acceptance[i] if i < len(acceptance) else None,
                        "cik": cik,
                    }
                ),
            }
        )
    return out


def ingest_real_dataset(
    *,
    tickers: list[str],
    start: date,
    end: date,
    out_dir: str | Path,
    cache_dir: str | Path,
    tiingo_api_key: str,
    sec_user_agent: str,
    alphavantage_api_key: str,
    offline: bool = False,
    price_provider: str = "tiingo",
    survivorship_biased: bool = True,
) -> DatasetPaths:
    if price_provider != "tiingo":
        raise ValueError("Only price_provider='tiingo' is currently supported")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prices_path = out / "prices.csv"
    events_path = out / "events.csv"
    metadata_path = out / "metadata.json"

    client = CachedHTTP(cache_dir=cache_dir, offline=offline)
    ticker_map = load_sec_ticker_map(client, sec_user_agent)

    all_prices: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    for ticker in [t.upper() for t in tickers]:
        all_prices.extend(load_tiingo_prices(client, ticker, start, end, api_key=tiingo_api_key))
        cik = ticker_map.get(ticker)
        if cik is not None:
            earnings = load_alpha_vantage_earnings(client, ticker, alphavantage_api_key)
            all_events.extend(load_sec_8k_events(client, ticker, cik, start, end, sec_user_agent, earnings))

    all_prices.sort(key=lambda r: (r["timestamp"], r["symbol"]))
    all_events.sort(key=lambda r: (r["timestamp"], r["event_id"]))

    symbol_first_ts: dict[str, datetime] = {}
    symbol_last_ts: dict[str, datetime] = {}
    for row in all_prices:
        symbol = str(row["symbol"]).upper()
        ts = _iso_to_utc(str(row["timestamp"]))
        symbol_first_ts[symbol] = min(symbol_first_ts.get(symbol, ts), ts)
        symbol_last_ts[symbol] = max(symbol_last_ts.get(symbol, ts), ts)

    expected_last_trading_day = end
    while expected_last_trading_day.weekday() >= 5:
        expected_last_trading_day -= timedelta(days=1)

    symbol_lifetimes: dict[str, dict[str, str | None]] = {}
    for symbol, listed_at in symbol_first_ts.items():
        last_ts = symbol_last_ts[symbol]
        delisted_at: datetime | None = None
        if not survivorship_biased and last_ts.date() < expected_last_trading_day:
            delisted_at = last_ts
        symbol_lifetimes[symbol] = {
            "listed_at": listed_at.isoformat(),
            "delisted_at": delisted_at.isoformat() if delisted_at else None,
        }

    for row in all_prices:
        symbol = str(row["symbol"]).upper()
        lifetime = symbol_lifetimes[symbol]
        row["listed_at"] = lifetime["listed_at"] or ""
        row["delisted_at"] = lifetime["delisted_at"] or ""

    with prices_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "symbol",
                "price",
                "bid",
                "ask",
                "volume",
                "listed_at",
                "delisted_at",
            ],
        )
        writer.writeheader()
        writer.writerows(all_prices)

    with events_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"]
        )
        writer.writeheader()
        writer.writerows(all_events)

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers": [t.upper() for t in tickers],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "price_provider": price_provider,
        "event_provider": "sec_submissions_8k_item_2_02 + alphavantage_earnings",
        "offline": offline,
        "survivorship_biased": survivorship_biased,
        "survivorship_note": (
            "Universe may exclude delisted symbols unless explicitly provided. "
            "Treat results as survivorship-biased."
            if survivorship_biased
            else "Universe explicitly includes delisted symbols."
        ),
        "rows": {"prices": len(all_prices), "events": len(all_events)},
        "symbol_lifetimes": symbol_lifetimes,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return DatasetPaths(prices_path=prices_path, events_path=events_path, metadata_path=metadata_path)


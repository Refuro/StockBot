"""Ticket 2d — a REAL live DataFeed for forward paper trading (daily cadence).

Replaces the fake paper feed with live sources:
- Quotes: Tiingo IEX last (paid tier), falling back to the latest EOD close.
- Events: SEC submissions polling for new 8-K Item 2.02 filings, joined with a
  fresh Alpha Vantage earnings pull for the names that just reported — the
  exact same join (`load_sec_8k_events`) the historical dataset builder uses,
  so payload shape and surprise semantics are identical.

PIT is trivial (now is now); `get_events` keeps consume-once cursor semantics:
each event is delivered exactly once across process runs via a persisted
cursor + seen-id state file. The cursor only advances in `advance()` — i.e.
after the tick that consumed the events completed — so a crashed run re-delivers
rather than drops (entries are idempotent per symbol on the strategy side).

Freshness vs caching: all HTTP goes through `CachedHTTP` rooted at a per-day
cache directory (`<cache_root>/YYYY-MM-DD/`), so intra-day retries are free
but every new day refetches.

The historical feed is untouched.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from stockbot.core.datafeed.base import DataFeed
from stockbot.data.real_loader import (
    TIINGO_DAILY_URL,
    CachedHTTP,
    load_alpha_vantage_earnings,
    load_sec_8k_events,
    load_sec_ticker_map,
)
from stockbot.types import MarketEvent, Quote

logger = logging.getLogger(__name__)

TIINGO_IEX_URL = "https://api.tiingo.com/iex/"

MAX_SEEN_IDS = 1000


class LiveDataFeed(DataFeed):
    """Daily-cadence live feed: Tiingo prices + SEC 8-K polling + AV earnings."""

    def __init__(
        self,
        *,
        symbols: list[str],
        cache_root: str | Path = "data/live_cache",
        state_path: str | Path = "data/live_feed_state.json",
        tiingo_api_key: str,
        alphavantage_api_key: str,
        sec_user_agent: str,
        lookback_days: int = 7,
        request_sleep_s: float = 0.15,
        clock: Callable[[], datetime] | None = None,
        client: CachedHTTP | None = None,
    ) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._state_path = Path(state_path)
        self._tiingo_key = tiingo_api_key
        self._av_key = alphavantage_api_key
        self._sec_user_agent = sec_user_agent
        self._lookback_days = lookback_days
        self._sleep_s = request_sleep_s
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if client is not None:
            self._client = client
        else:
            day_dir = Path(cache_root) / self._clock().date().isoformat()
            self._client = CachedHTTP(cache_dir=day_dir, offline=False)
        self._state = self._load_state()
        # Events delivered this process run; committed to `seen` in advance().
        self._delivered_ids: list[str] = []
        self._ticked = False

    # --- state ----------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        if self._state_path.exists():
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        cursor = (self._clock() - timedelta(days=self._lookback_days)).isoformat()
        return {"cursor": cursor, "seen_event_ids": []}

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    # --- DataFeed interface ------------------------------------------------------

    def now(self) -> datetime:
        return self._clock()

    def get_quote(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        now = self.now()
        # IEX last (paid tier) first.
        try:
            rows = self._client.get_json(
                TIINGO_IEX_URL, params={"tickers": symbol, "token": self._tiingo_key}
            )
            if isinstance(rows, list) and rows:
                row = rows[0]
                price = row.get("tngoLast") or row.get("last") or row.get("prevClose")
                if price:
                    return Quote(
                        symbol=symbol,
                        price=float(price),
                        bid=row.get("bidPrice"),
                        ask=row.get("askPrice"),
                        volume=int(row["volume"]) if row.get("volume") else None,
                        as_of=now,
                    )
        except Exception as exc:  # noqa: BLE001 - fall through to EOD
            logger.warning("IEX quote failed for %s: %s", symbol, exc)
        # EOD fallback: latest daily close in the past 10 days.
        rows = self._client.get_json(
            TIINGO_DAILY_URL.format(ticker=symbol),
            params={
                "startDate": (now.date() - timedelta(days=10)).isoformat(),
                "endDate": now.date().isoformat(),
                "resampleFreq": "daily",
                "token": self._tiingo_key,
            },
        )
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"No live price available for {symbol}")
        last = rows[-1]
        price = last.get("adjClose") or last.get("close")
        if not price:
            raise ValueError(f"No usable close for {symbol}")
        return Quote(
            symbol=symbol,
            price=float(price),
            volume=int(last["adjVolume"]) if last.get("adjVolume") else None,
            as_of=now,
        )

    def get_events(self, since: datetime | None = None) -> list[MarketEvent]:
        now = self.now()
        cursor = datetime.fromisoformat(self._state["cursor"])
        if since is not None:
            cursor = max(cursor, since)
        seen: set[str] = set(self._state.get("seen_event_ids", []))

        try:
            ticker_map = load_sec_ticker_map(self._client, self._sec_user_agent)
        except Exception as exc:  # noqa: BLE001
            logger.error("SEC ticker map unavailable: %s", exc)
            return []

        start = cursor.date()
        end = now.date()
        out: list[MarketEvent] = []
        for symbol in self._symbols:
            cik = ticker_map.get(symbol)
            if cik is None:
                continue
            try:
                fresh = self._fresh_8k_dates(cik, start, end)
            except Exception as exc:  # noqa: BLE001 - per-symbol isolation
                logger.warning("submissions poll failed for %s: %s", symbol, exc)
                continue
            if not fresh:
                continue
            # AV earnings refresh only for names that just reported.
            try:
                earnings = load_alpha_vantage_earnings(self._client, symbol, self._av_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("AV earnings refresh failed for %s: %s", symbol, exc)
                continue
            try:
                rows = load_sec_8k_events(
                    self._client, symbol, cik, start, end, self._sec_user_agent, earnings
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("8-K event build failed for %s: %s", symbol, exc)
                continue
            for row in rows:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts <= cursor or ts > now or row["event_id"] in seen:
                    continue
                out.append(
                    MarketEvent(
                        event_id=row["event_id"],
                        event_type=row["event_type"],
                        symbol=row["symbol"],
                        as_of=ts,
                        payload=json.loads(row["payload_json"]),
                    )
                )
            time.sleep(self._sleep_s)

        out.sort(key=lambda e: e.as_of)
        self._delivered_ids.extend(e.event_id for e in out)
        return out

    def _fresh_8k_dates(self, cik: int, start: date, end: date) -> list[date]:
        """Filing dates of 8-K Item 2.02 filings in [start, end] (cheap pre-check)."""
        from stockbot.data.real_loader import SEC_SUBMISSIONS_URL, _is_item_202

        raw = self._client.get_json(
            SEC_SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": self._sec_user_agent}
        )
        recent = raw.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        items = recent.get("items", [])
        out = []
        for i, form in enumerate(forms):
            if not str(form).startswith("8-K"):
                continue
            if not _is_item_202(items[i] if i < len(items) else ""):
                continue
            fd = date.fromisoformat(filing_dates[i])
            if start <= fd <= end:
                out.append(fd)
        return out

    def get_universe(self, as_of: datetime | None = None) -> list[str]:
        return list(self._symbols)

    def advance(self) -> bool:
        """One tick per process run. Commits the event cursor on completion."""
        if not self._ticked:
            self._ticked = True
            if self._delivered_ids:
                seen = self._state.get("seen_event_ids", [])
                seen.extend(self._delivered_ids)
                self._state["seen_event_ids"] = seen[-MAX_SEEN_IDS:]
            self._state["cursor"] = self.now().isoformat()
            self._save_state()
        return False

"""CSV-backed historical replay data feed with strict point-in-time semantics."""

from __future__ import annotations

import csv
import json
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path

from stockbot.core.datafeed.base import DataFeed, PointInTimeViolation
from stockbot.types import MarketEvent, Quote


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


class HistoricalReplayFeed(DataFeed):
    """Replays historical quotes/events from CSV sources as-of a simulated clock."""

    def __init__(
        self,
        quotes: list[Quote],
        events: list[MarketEvent],
        symbol_lifetimes: dict[str, tuple[datetime, datetime | None]] | None = None,
    ) -> None:
        if not quotes and not events:
            raise ValueError("HistoricalReplayFeed requires at least one quote or event")
        self._quotes = sorted(quotes, key=lambda q: q.as_of)
        self._events = sorted(events, key=lambda e: e.as_of)
        self._event_times = [e.as_of for e in self._events]
        self._next_event_idx = 0
        self._quotes_by_symbol: dict[str, list[Quote]] = {}
        for quote in self._quotes:
            self._quotes_by_symbol.setdefault(quote.symbol, []).append(quote)

        inferred_lifetimes: dict[str, tuple[datetime, datetime | None]] = {}
        for symbol, symbol_quotes in self._quotes_by_symbol.items():
            inferred_lifetimes[symbol] = (symbol_quotes[0].as_of, None)
        self._symbol_lifetimes = inferred_lifetimes
        if symbol_lifetimes:
            for symbol, lifetime in symbol_lifetimes.items():
                self._symbol_lifetimes[symbol] = lifetime

        times = {q.as_of for q in self._quotes} | {e.as_of for e in self._events}
        self._timeline = sorted(times)
        self._index = 0

    @classmethod
    def from_csv(cls, prices_path: str | Path, events_path: str | Path) -> HistoricalReplayFeed:
        price_rows, lifetimes = cls._load_price_rows(prices_path)
        event_rows = cls._load_event_rows(events_path)
        return cls(price_rows, event_rows, symbol_lifetimes=lifetimes)

    @staticmethod
    def _load_price_rows(path: str | Path) -> tuple[list[Quote], dict[str, tuple[datetime, datetime | None]]]:
        prices: list[Quote] = []
        first_quote: dict[str, datetime] = {}
        last_quote: dict[str, datetime] = {}
        listed_overrides: dict[str, datetime] = {}
        delisted_overrides: dict[str, datetime] = {}
        with Path(path).open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = _parse_ts(row["timestamp"])
                symbol = row["symbol"]
                first_quote[symbol] = min(first_quote.get(symbol, ts), ts)
                last_quote[symbol] = max(last_quote.get(symbol, ts), ts)
                if row.get("listed_at"):
                    listed_overrides[symbol] = _parse_ts(row["listed_at"])
                if row.get("delisted_at"):
                    delisted_overrides[symbol] = _parse_ts(row["delisted_at"])
                prices.append(
                    Quote(
                        symbol=symbol,
                        price=float(row["price"]),
                        bid=float(row["bid"]) if row.get("bid") else None,
                        ask=float(row["ask"]) if row.get("ask") else None,
                        volume=int(row["volume"]) if row.get("volume") else None,
                        as_of=ts,
                    )
                )
        lifetimes: dict[str, tuple[datetime, datetime | None]] = {}
        symbols = set(first_quote) | set(listed_overrides)
        for symbol in symbols:
            listed_at = listed_overrides.get(symbol, first_quote.get(symbol))
            if listed_at is None:
                continue
            delisted_at = delisted_overrides.get(symbol)
            lifetimes[symbol] = (listed_at, delisted_at)
        return prices, lifetimes

    @staticmethod
    def _load_event_rows(path: str | Path) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        p = Path(path)
        if not p.exists():
            return events
        with p.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = _parse_ts(row["timestamp"])
                payload_raw = row.get("payload_json") or "{}"
                payload = json.loads(payload_raw)
                events.append(
                    MarketEvent(
                        event_id=row["event_id"],
                        event_type=row["event_type"],
                        symbol=row["symbol"],
                        as_of=ts,
                        payload=payload,
                    )
                )
        return events

    def now(self) -> datetime:
        return self._timeline[self._index]

    def get_quote_at(self, symbol: str, as_of: datetime) -> Quote:
        """Return quote as-of `as_of`, but reject any attempt to peek beyond now()."""
        if as_of > self.now():
            raise PointInTimeViolation(
                f"Attempted to peek quote for {symbol} at {as_of.isoformat()} > now {self.now().isoformat()}"
            )
        quotes = self._quotes_by_symbol.get(symbol, [])
        eligible = [q for q in quotes if q.as_of <= as_of]
        if not eligible:
            raise ValueError(f"No quote for {symbol} as of {as_of.isoformat()}")
        quote = eligible[-1]
        self.validate_point_in_time(quote.as_of)
        return quote

    def get_quote(self, symbol: str) -> Quote:
        return self.get_quote_at(symbol, self.now())

    def get_events(self, since: datetime | None = None) -> list[MarketEvent]:
        current = self.now()
        if since is None:
            visible: list[MarketEvent] = []
            while self._next_event_idx < len(self._events):
                event = self._events[self._next_event_idx]
                if event.as_of > current:
                    break
                visible.append(event)
                self._next_event_idx += 1
        else:
            start = bisect_left(self._event_times, since)
            end = bisect_right(self._event_times, current)
            visible = self._events[start:end]
        for event in visible:
            self.validate_point_in_time(event.as_of)
        return visible

    def get_universe(self, as_of: datetime | None = None) -> list[str]:
        current = as_of or self.now()
        self.validate_point_in_time(current)
        universe: list[str] = []
        for symbol, (listed_at, delisted_at) in sorted(self._symbol_lifetimes.items()):
            if listed_at > current:
                continue
            if delisted_at is not None and current > delisted_at:
                continue
            universe.append(symbol)
        return universe

    def advance(self) -> bool:
        if self._index >= len(self._timeline) - 1:
            return False
        self._index += 1
        return True

    def rewind_to(self, ts: datetime) -> None:
        """Set timeline index to latest step <= ts."""
        idx = bisect_right(self._timeline, ts) - 1
        if idx < 0:
            raise ValueError(f"Cannot rewind before first timestamp: {ts.isoformat()}")
        self._index = idx
        self._next_event_idx = bisect_right(self._event_times, self.now())

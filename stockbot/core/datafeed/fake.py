"""Fake data feeds for Phase 0 smoke testing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stockbot.core.datafeed.base import DataFeed
from stockbot.types import MarketEvent, Quote

FAKE_SYMBOL = "FAKE"
FAKE_BASE_PRICE = 100.0


class FakeReplayFeed(DataFeed):
    """Historical replay with a deterministic fake price series."""

    def __init__(
        self,
        start: datetime | None = None,
        steps: int = 5,
        step_hours: int = 24,
        quote_time_skew_hours: int = 0,
    ) -> None:
        self._start = start or datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc)
        self._steps = steps
        self._step_hours = step_hours
        self._quote_time_skew_hours = quote_time_skew_hours
        self._index = 0
        self._events = self._build_events()
        self._next_event_idx = 0

    def _build_events(self) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        for i in range(self._steps):
            ts = self._start + timedelta(hours=i * self._step_hours)
            events.append(
                MarketEvent(
                    event_id=f"fake-evt-{i}",
                    event_type="tick",
                    symbol=FAKE_SYMBOL,
                    as_of=ts,
                    payload={"step": i},
                )
            )
        return events

    def now(self) -> datetime:
        if self._index >= len(self._events):
            return self._events[-1].as_of
        return self._events[self._index].as_of

    def get_quote(self, symbol: str) -> Quote:
        if symbol != FAKE_SYMBOL:
            raise ValueError(f"FakeReplayFeed only supports {FAKE_SYMBOL}")
        ts = self.now() + timedelta(hours=self._quote_time_skew_hours)
        price = FAKE_BASE_PRICE + self._index * 0.5
        quote = Quote(symbol=symbol, price=price, bid=price - 0.01, ask=price + 0.01, volume=1000, as_of=ts)
        self.validate_point_in_time(quote.as_of)
        return quote

    def get_events(self, since: datetime | None = None) -> list[MarketEvent]:
        current = self.now()
        if since is not None:
            visible = [e for e in self._events if since <= e.as_of <= current]
        else:
            visible = []
            while self._next_event_idx < len(self._events):
                evt = self._events[self._next_event_idx]
                if evt.as_of > current:
                    break
                visible.append(evt)
                self._next_event_idx += 1
        for e in visible:
            self.validate_point_in_time(e.as_of)
        return visible

    def get_universe(self, as_of: datetime | None = None) -> list[str]:
        ts = as_of or self.now()
        self.validate_point_in_time(ts)
        return [FAKE_SYMBOL]

    def advance(self) -> bool:
        if self._index >= self._steps - 1:
            return False
        self._index += 1
        return True


class FakeLiveFeed(DataFeed):
    """Live-style feed returning current fake data (for paper/live smoke tests)."""

    def __init__(self) -> None:
        self._now = datetime.now(timezone.utc)
        self._returned_once = False

    def now(self) -> datetime:
        return self._now

    def get_quote(self, symbol: str) -> Quote:
        if symbol != FAKE_SYMBOL:
            raise ValueError(f"FakeLiveFeed only supports {FAKE_SYMBOL}")
        quote = Quote(
            symbol=symbol,
            price=FAKE_BASE_PRICE,
            bid=FAKE_BASE_PRICE - 0.01,
            ask=FAKE_BASE_PRICE + 0.01,
            volume=5000,
            as_of=self._now,
        )
        self.validate_point_in_time(quote.as_of)
        return quote

    def get_events(self, since: datetime | None = None) -> list[MarketEvent]:
        evt = MarketEvent(
            event_id="fake-live-0",
            event_type="tick",
            symbol=FAKE_SYMBOL,
            as_of=self._now,
            payload={"live": True},
        )
        self.validate_point_in_time(evt.as_of)
        if since is not None and evt.as_of < since:
            return []
        if since is None and self._returned_once:
            return []
        self._returned_once = True
        return [evt]

    def get_universe(self, as_of: datetime | None = None) -> list[str]:
        ts = as_of or self.now()
        self.validate_point_in_time(ts)
        return [FAKE_SYMBOL]

    def advance(self) -> bool:
        return False

"""DataFeed abstract interface — point-in-time market data and events."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from stockbot.types import MarketEvent, Quote


class DataFeed(ABC):
    """Provides market data and events as-of a simulated or live timestamp."""

    @abstractmethod
    def now(self) -> datetime:
        """Current as-of timestamp for this feed."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Return the latest quote knowable as of now()."""

    @abstractmethod
    def get_events(self, since: datetime | None = None) -> list[MarketEvent]:
        """Return events knowable as of now(), optionally filtered by since."""

    @abstractmethod
    def get_universe(self, as_of: datetime | None = None) -> list[str]:
        """Return symbols listed as-of timestamp (defaults to now())."""

    @abstractmethod
    def advance(self) -> bool:
        """Advance the clock (backtest replay). Returns False when exhausted."""

    def validate_point_in_time(self, as_of: datetime) -> None:
        """Raise if data timestamp is after the feed's current now."""
        if as_of > self.now():
            raise PointInTimeViolation(
                f"Data dated {as_of.isoformat()} is after feed now {self.now().isoformat()}"
            )


class PointInTimeViolation(Exception):
    """Raised when data would leak future information."""

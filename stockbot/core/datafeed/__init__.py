"""DataFeed interface and implementations."""

from stockbot.core.datafeed.base import DataFeed
from stockbot.core.datafeed.fake import FakeLiveFeed, FakeReplayFeed
from stockbot.core.datafeed.historical import HistoricalReplayFeed
from stockbot.core.datafeed.factory import create_datafeed

__all__ = ["DataFeed", "FakeLiveFeed", "FakeReplayFeed", "HistoricalReplayFeed", "create_datafeed"]

"""DataFeed factory."""

from __future__ import annotations

from stockbot.config import AppConfig
from stockbot.core.datafeed.base import DataFeed
from stockbot.core.datafeed.fake import FakeLiveFeed, FakeReplayFeed
from stockbot.core.datafeed.historical import HistoricalReplayFeed


def create_datafeed(config: AppConfig) -> DataFeed:
    name = config.datafeed_name
    if name == "fake_replay":
        return FakeReplayFeed()
    if name == "fake_live":
        return FakeLiveFeed()
    if name == "historical_csv":
        cfg = config.historical_data
        return HistoricalReplayFeed.from_csv(cfg.prices_path, cfg.events_path)
    raise ValueError(f"Unknown datafeed: {name}")

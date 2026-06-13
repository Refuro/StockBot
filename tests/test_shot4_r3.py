"""Ticket 2-R3 — persist thesis_id on fills (shot 4).

Three things must hold:
1. The additive migration upgrades an existing pre-2-R3 database in place
   (legacy fill rows keep thesis_id NULL).
2. New fills written through the central plumbing (`submit_and_record` and the
   next-bar `settle_pending_orders` path) carry the originating thesis_id.
3. The dashboard drill-down uses the DIRECT join — the old window match was
   exact only because this strategy family holds ≤1 open trade per symbol per
   lane, and silently broke otherwise. Legacy NULL rows still fall back.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stockbot.bots.proportionality.ride import BOT_ID, ProportionalityRideStrategy, RideConfig
from stockbot.config import AppConfig, SimulatedBrokerConfig
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.dashboard.data import thesis_detail, thesis_id_for_fill
from stockbot.harness.loop import drive_strategy
from stockbot.types import ExecutionMode

UTC = timezone.utc

# The fills schema exactly as it existed before 2-R3 (no thesis_id column).
_OLD_FILLS_SCHEMA = """
CREATE TABLE fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    slippage_pct REAL NOT NULL DEFAULT 0,
    commission REAL NOT NULL DEFAULT 0,
    bot_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    filled_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _write_dataset(data_dir: Path, days: int = 16) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume"])
        writer.writeheader()
        for day in range(days):
            ts = start + timedelta(days=day)
            writer.writerow(
                {
                    "timestamp": ts.isoformat(),
                    "symbol": "AAA",
                    "price": f"{50 * (1.01 ** day):.4f}",
                    "bid": "",
                    "ask": "",
                    "volume": 200_000,
                }
            )
    with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        writer.writeheader()
        writer.writerow(
            {
                "event_id": "AAA-earnings-1",
                "event_type": "earnings",
                "symbol": "AAA",
                "timestamp": (start + timedelta(days=2, hours=1, minutes=30)).isoformat(),
                "payload_json": json.dumps({"actual_eps": 1.2, "consensus_eps": 1.0, "surprise_pct": 0.2}),
            }
        )


def _cfg(data_dir: Path, db_path: Path) -> AppConfig:
    cfg = AppConfig.model_validate(
        {
            "mode": ExecutionMode.BACKTEST,
            "datafeed": {"backtest": "historical_csv", "paper": "fake_live", "live": "fake_live"},
            "broker": {"backtest": "simulated", "paper": "simulated", "live": "simulated"},
            "historical_data": {
                "prices_path": str(data_dir / "prices.csv"),
                "events_path": str(data_dir / "events.csv"),
            },
            "state": {"db_path": str(db_path)},
        }
    )
    cfg.simulated_broker = SimulatedBrokerConfig(initial_cash=100_000.0, execution_timing="next_bar")
    return cfg


class TestMigration:
    def test_existing_db_migrates_in_place_and_legacy_rows_stay_null(self, tmp_path) -> None:
        db_path = tmp_path / "old.db"
        with sqlite3.connect(db_path) as conn:
            conn.executescript(_OLD_FILLS_SCHEMA)
            conn.execute(
                """
                INSERT INTO fills (fill_id, order_id, symbol, side, quantity, price, bot_id, mode, filled_at)
                VALUES ('legacy-fill', 'legacy-order', 'AAA', 'buy', 10, 50.0, 'bot', 'paper',
                        '2026-06-01T20:00:00+00:00')
                """
            )
        store = StateStore(db_path)  # migration runs in _init_schema
        with store.transaction() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(fills)")}
            assert "thesis_id" in cols
            row = conn.execute("SELECT thesis_id FROM fills WHERE fill_id='legacy-fill'").fetchone()
            assert row["thesis_id"] is None
        # Idempotent: opening the store again must not fail or duplicate.
        StateStore(db_path)

    def test_fresh_db_has_the_column(self, tmp_path) -> None:
        store = StateStore(tmp_path / "fresh.db")
        with store.transaction() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(fills)")}
        assert "thesis_id" in cols


class TestFillsCarryThesisId:
    def test_entry_and_exit_fills_carry_thesis_id_through_next_bar_settle(self, tmp_path) -> None:
        """End to end on the real loop: entry settles via settle_pending_orders,
        the time-stop exit via the same path — both fills must join directly."""
        data_dir = tmp_path / "data"
        _write_dataset(data_dir)
        db_path = tmp_path / "state.db"
        cfg = _cfg(data_dir, db_path)
        ride = RideConfig(entry_mode="threshold", surprise_threshold=0.05, hold_days=3)

        def factory(_comp) -> Strategy:
            return ProportionalityRideStrategy(ride)

        drive_strategy(factory, cfg)
        store = StateStore(db_path)
        fills = store.get_fills(BOT_ID, "backtest")
        assert len(fills) == 2, "expected one entry and one exit fill"
        thesis_ids = {f["thesis_id"] for f in fills}
        assert len(thesis_ids) == 1 and None not in thesis_ids
        with store.transaction() as conn:
            row = conn.execute(
                "SELECT thesis_id FROM theses WHERE thesis_id = ?", (thesis_ids.pop(),)
            ).fetchone()
        assert row is not None, "fill thesis_id must reference a real thesis row"


class TestDashboardDirectJoin:
    def _seed(self, db_path: Path) -> StateStore:
        """Two sequential theses on the SAME symbol/lane — the case the old
        window match got wrong (it always picked the nearest prior thesis)."""
        store = StateStore(db_path)
        t0 = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        store.record_thesis(
            thesis_id="thesis-A", bot_id="bot", symbol="AAA", direction="long",
            stated_probability=0.6, mode="paper", created_at=t0,
        )
        store.record_thesis(
            thesis_id="thesis-B", bot_id="bot", symbol="AAA", direction="long",
            stated_probability=0.6, mode="paper", created_at=t0 + timedelta(days=2),
        )
        # Fill belongs to thesis A but lands AFTER thesis B was created.
        store.record_fill(
            fill_id="fill-direct", order_id="o1", symbol="AAA", side="sell",
            quantity=10, price=55.0, slippage_pct=0.0, bot_id="bot", mode="paper",
            filled_at=t0 + timedelta(days=3), thesis_id="thesis-A",
        )
        # Legacy row (NULL thesis_id) in the same spot — fallback must window-match.
        store.record_fill(
            fill_id="fill-legacy", order_id="o2", symbol="AAA", side="buy",
            quantity=10, price=50.0, slippage_pct=0.0, bot_id="bot", mode="paper",
            filled_at=t0 + timedelta(days=3),
        )
        return store

    def test_drilldown_uses_direct_join_not_window_match(self, tmp_path) -> None:
        db_path = tmp_path / "dash.db"
        self._seed(db_path)
        # Direct join: the window match would have (wrongly) said thesis-B here.
        assert thesis_id_for_fill(db_path, "fill-direct") == "thesis-A"
        # Legacy NULL row still resolves via the fallback window match.
        assert thesis_id_for_fill(db_path, "fill-legacy") == "thesis-B"

    def test_thesis_detail_matches_fills_by_direct_join(self, tmp_path) -> None:
        db_path = tmp_path / "dash2.db"
        self._seed(db_path)
        detail = thesis_detail(db_path, "thesis-A")
        assert detail is not None
        assert [f["fill_id"] for f in detail["fills"]] == ["fill-direct"]
        # Thesis B has no directly-joined fills -> falls back to legacy window
        # match over NULL rows only (never steals thesis-A's tagged fill).
        detail_b = thesis_detail(db_path, "thesis-B")
        assert [f["fill_id"] for f in detail_b["fills"]] == ["fill-legacy"]

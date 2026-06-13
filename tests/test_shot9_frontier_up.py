"""Ticket 9e — Frontier UP-propagation (additive, pre-registered, long-tradeable).

Strictly isolated from the shot-8 down-only block (those numbers stay
reproducible — guarded by `tests/test_shot8_frontier.py`). These tests cover:
  - the pre-registered UP constants + helpers (separate buckets/trade-types);
  - the strategy UP lifecycle end-to-end: detect a POSITIVE anchor shock ->
    BUY the predicted-UP filer (≤3% equity) at T+1 -> resolve on the SAME
    sector-neutral excess and SELL to close;
  - the backtest UP enumeration (up signals, up per-bucket sign-accuracy);
  - paper-lane wiring: the up side activates only with an up detector and stays
    additive (down side untouched).
All offline: synthetic prices + injected detectors. No network, no LLM.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.frontier.propagation import (
    ANCHOR_UP_SHOCK_THRESHOLD,
    UP_DIRECTION_MAP,
    AnchorShock,
    FrontierConfig,
    FrontierPropagationStrategy,
    is_anchor_shock,
    is_anchor_up_shock,
    up_predicted_direction,
    up_strategy_bucket_for,
    up_trade_type_for,
)
from stockbot.config import AppConfig, SimulatedBrokerConfig
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.harness.loop import drive_strategy, drive_strategies
from stockbot.research import frontier_backtest as fb
from stockbot.types import ExecutionMode

UTC = timezone.utc
START = datetime(2026, 1, 5, 20, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Pre-registered UP constants / helpers
# ---------------------------------------------------------------------------


class TestUpConstants:
    def test_up_direction_map_is_the_falsifiable_hypothesis(self) -> None:
        assert up_predicted_direction("customer_concentration") == "up"
        assert up_predicted_direction("sole_supplier") == "up"
        assert up_predicted_direction("regulatory_precedent") is None
        assert "regulatory_precedent" not in UP_DIRECTION_MAP

    def test_up_shock_is_up_only_at_the_mirrored_threshold(self) -> None:
        assert ANCHOR_UP_SHOCK_THRESHOLD == 0.05  # mirrors the down magnitude
        assert is_anchor_up_shock(100.0, 105.0)       # +5% fires
        assert not is_anchor_up_shock(100.0, 104.0)   # +4% does not
        assert not is_anchor_up_shock(100.0, 92.0)    # down moves never fire (up-only)
        # And the up test is the mirror image of the down test (never both).
        assert is_anchor_shock(100.0, 92.0) and not is_anchor_up_shock(100.0, 92.0)

    def test_up_buckets_are_separate_and_never_commingled(self) -> None:
        assert up_strategy_bucket_for("customer_concentration") == "frontier:customer_concentration_up"
        assert up_strategy_bucket_for("sole_supplier") == "frontier:sole_supplier_up"
        assert up_trade_type_for("sole_supplier") == "frontier_sole_supplier_up"
        # Distinct from the down buckets (frontier:customer_concentration).
        from stockbot.bots.frontier.propagation import strategy_bucket_for
        assert up_strategy_bucket_for("customer_concentration") != strategy_bucket_for(
            "customer_concentration"
        )


# ---------------------------------------------------------------------------
# Strategy UP lifecycle: detect -> BUY (T+1) -> resolve + SELL
# ---------------------------------------------------------------------------


def _frontier_cfg(data_dir: Path, db_path: Path) -> AppConfig:
    cfg = AppConfig.model_validate({
        "mode": ExecutionMode.BACKTEST,
        "datafeed": {"backtest": "historical_csv", "paper": "fake_live", "live": "fake_live"},
        "broker": {"backtest": "simulated", "paper": "simulated", "live": "simulated"},
        "historical_data": {
            "prices_path": str(data_dir / "prices.csv"),
            "events_path": str(data_dir / "events.csv"),
        },
        "state": {"db_path": str(db_path)},
    })
    cfg.simulated_broker = SimulatedBrokerConfig(
        initial_cash=100_000.0, execution_timing="next_bar", slippage_pct=0.0, cost_profile="flat",
    )
    return cfg


def _write_feed_dataset(data_dir: Path, series: dict[str, list[float]], start: datetime = START) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume"])
        w.writeheader()
        for sym, closes in series.items():
            for day, px in enumerate(closes):
                w.writerow({
                    "timestamp": (start + timedelta(days=day)).isoformat(),
                    "symbol": sym, "price": f"{px:.4f}", "bid": "", "ask": "", "volume": 500_000,
                })
    with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"]).writeheader()


def _up_shock_detector_on(day_index: int, anchor: str, *, start: datetime = START):
    shock_date = (start + timedelta(days=day_index)).date()

    def detect(now: datetime) -> list[AnchorShock]:
        if now.date() != shock_date:
            return []
        return [AnchorShock(anchor_symbol=anchor, as_of=now, prev_close=100.0, close=108.0, move_pct=0.08)]

    return detect


class TestUpStrategyLifecycle:
    def test_up_signal_buys_next_bar_and_resolves_sign_correct(self, tmp_path: Path) -> None:
        # FLR RISES ~4% over the window while peers are flat -> UP prediction wins.
        closes = {
            "FLR":   [50, 50, 50, 50, 50, 50.5, 51.0, 51.5, 52.0, 52.0, 52.0],
            "PEERA": [10] * 11,
            "PEERB": [20] * 11,
        }
        data_dir = tmp_path / "data"
        _write_feed_dataset(data_dir, closes)
        db_path = tmp_path / "state.db"
        cfg = _frontier_cfg(data_dir, db_path)

        edges = [{
            "edge_type": "customer_concentration", "from_symbol": "FLR",
            "counterparty_symbol": "ANC", "filed_at": "2026-01-01", "magnitude_pct": 30.0,
        }]

        def factory(_comp) -> Strategy:
            return FrontierPropagationStrategy(
                FrontierConfig(hold_trading_days=5, up_enabled=True),
                up_anchor_shock_detector=_up_shock_detector_on(2, "ANC"),
                sector_members={"FLR": ["PEERA", "PEERB"]},
                edges=edges,
            )

        drive_strategy(factory, cfg)
        store = StateStore(db_path)

        signals = store.get_decisions("frontier", "backtest", "signal")
        assert [d["action"] for d in signals] == ["anchor_up_shock_detected"]
        entries = store.get_decisions("frontier", "backtest", "entry")
        assert [d["action"] for d in entries] == ["up_prediction_opened"]
        assert entries[0]["inputs"]["quantity"] > 0  # a real BUY was sized
        resolutions = store.get_decisions("frontier", "backtest", "resolution")
        assert len(resolutions) == 1
        res = resolutions[0]
        assert res["action"] == "sign_correct_up"
        assert res["inputs"]["sector_neutral"] is True
        assert res["inputs"]["strategy_bucket"] == "frontier:customer_concentration_up"

        with store.transaction() as conn:
            cal = conn.execute(
                "SELECT strategy_bucket, outcome, trade_type FROM calibration_ledger"
            ).fetchall()
        assert len(cal) == 1
        assert cal[0]["strategy_bucket"] == "frontier:customer_concentration_up"
        assert cal[0]["outcome"] == 1

        # The long-tradeable claim: a BUY and a closing SELL were actually filled.
        with store.transaction() as conn:
            sides = [r["side"] for r in conn.execute(
                "SELECT side FROM fills WHERE bot_id = 'frontier' ORDER BY filled_at"
            ).fetchall()]
        assert "buy" in sides and "sell" in sides

    def test_down_side_untouched_when_up_disabled(self, tmp_path: Path) -> None:
        # up_enabled defaults False -> NO up activity even with an up detector
        # wired; this is the bit-identical shot-8 reproduction guarantee.
        closes = {"FLR": [50] * 8, "PEERA": [10] * 8}
        data_dir = tmp_path / "data"
        _write_feed_dataset(data_dir, closes)
        db_path = tmp_path / "state.db"
        cfg = _frontier_cfg(data_dir, db_path)
        edges = [{
            "edge_type": "customer_concentration", "from_symbol": "FLR",
            "counterparty_symbol": "ANC", "filed_at": "2026-01-01", "magnitude_pct": 30.0,
        }]

        def factory(_comp) -> Strategy:
            return FrontierPropagationStrategy(
                FrontierConfig(hold_trading_days=5),  # up_enabled defaults False
                up_anchor_shock_detector=_up_shock_detector_on(2, "ANC"),
                sector_members={"FLR": ["PEERA"]}, edges=edges,
            )

        drive_strategy(factory, cfg)
        store = StateStore(db_path)
        assert store.get_decisions("frontier", "backtest", "signal") == []


# ---------------------------------------------------------------------------
# Backtest UP enumeration + per-bucket sign accuracy (the measurement)
# ---------------------------------------------------------------------------


def _write_prices(path: Path, series: dict[str, list[tuple[date, float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume", "listed_at", "delisted_at"])
        w.writeheader()
        for sym, rows in series.items():
            for d, px in rows:
                w.writerow({
                    "timestamp": datetime(d.year, d.month, d.day, 20, tzinfo=UTC).isoformat(),
                    "symbol": sym, "price": f"{px:.4f}", "bid": "", "ask": "",
                    "volume": 1_000_000, "listed_at": "", "delisted_at": "",
                })


def _daily(start: date, closes: list[float]) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), c) for i, c in enumerate(closes)]


class TestUpBacktest:
    def test_up_signal_enumeration_and_sign_accuracy(self, tmp_path: Path) -> None:
        d0 = date(2026, 2, 2)
        # Anchor ANC: flat then a +8% UP shock on day 5.
        anchor = _daily(d0, [100, 100, 100, 100, 100, 108, 108, 108, 108, 108, 108, 108])
        # Filer FLR: flat to entry (day6), then RISES 6% over the 5-day window.
        filer = _daily(d0, [50, 50, 50, 50, 50, 50, 50, 50.5, 51.0, 51.5, 52.0, 53.0])
        peera = _daily(d0, [10] * 12)
        peerb = _daily(d0, [20] * 12)

        anchor_csv = tmp_path / "anchor.csv"
        _write_prices(anchor_csv, {"ANC": anchor})
        uni_csv = tmp_path / "universe.csv"
        _write_prices(uni_csv, {"FLR": filer, "PEERA": peera, "PEERB": peerb})

        edges = [{
            "edge_type": "customer_concentration", "from_symbol": "FLR",
            "counterparty_symbol": "ANC", "filed_at": "2026-01-01", "magnitude_pct": 30.0,
        }]
        anchor_series = fb.load_price_series(anchor_csv, {"ANC"})

        # No DOWN signal (the move is up); exactly one UP signal.
        assert fb.enumerate_signals(edges, anchor_series) == []
        up_signals = fb.enumerate_up_signals(edges, anchor_series)
        assert len(up_signals) == 1
        assert up_signals[0].direction == "up" and up_signals[0].filer == "FLR"

        symbol_sector = {"FLR": "Tech", "PEERA": "Tech", "PEERB": "Tech"}
        sector_members = {"Tech": ["FLR", "PEERA", "PEERB"]}
        uni_series = fb.load_price_series(uni_csv)
        resolved = fb.backtest_signals(up_signals, uni_series, symbol_sector, sector_members)
        assert len(resolved) == 1
        r = resolved[0]
        # Entry T+1 = day6 (50), exit +5 trading days = day11 (53): +6%, peers flat.
        assert r.filer_return == pytest.approx((53.0 - 50.0) / 50.0)
        assert r.excess_return == pytest.approx(0.06)
        assert r.outcome is True  # UP prediction correct (excess > 0)

        buckets = fb.sign_accuracy_by_bucket(resolved, up=True)
        assert "customer_concentration_up" in buckets
        assert buckets["customer_concentration_up"]["n"] == 1
        assert buckets["customer_concentration_up"]["sign_accuracy"] == 1.0
        assert buckets["customer_concentration_up"]["trade_type"] == "frontier_customer_concentration_up"


# ---------------------------------------------------------------------------
# Paper-lane wiring: up side is additive and activates only with an up detector
# ---------------------------------------------------------------------------


class _Feed:
    def __init__(self) -> None:
        self._t = datetime(2026, 2, 2, 20, tzinfo=UTC)

    def now(self):
        return self._t

    def get_events(self, since=None):
        return []

    def get_quote(self, symbol):
        from stockbot.types import Quote
        return Quote(symbol=symbol, price=10.0, as_of=self._t)

    def get_universe(self, as_of=None):
        return ["FLR", "PEER"]

    def advance(self):
        return False


def _graph_with_edge(tmp_path: Path) -> Path:
    graph_db = tmp_path / "graph.db"
    gstore = StateStore(graph_db)
    gstore.record_frontier_edge(
        edge_id="e1", edge_type="customer_concentration", from_symbol="FLR",
        counterparty_name="Anchor Inc", counterparty_symbol="ANC", named=True,
        magnitude_pct=30.0, quoted_passage="q", accession_number="a",
        filing_url="u", form="10-K", filed_at="2026-01-01",
        extracted_at=datetime(2026, 1, 2, tzinfo=UTC), model_id="m",
    )
    return graph_db


class TestUpPaperWiring:
    def test_up_side_records_signal_when_up_detector_present(self, tmp_path: Path) -> None:
        from stockbot.harness import paper_run

        graph_db = _graph_with_edge(tmp_path)
        meta = tmp_path / "meta.json"
        meta.write_text(
            '{"symbols":[{"symbol":"FLR","sector":"Tech"},{"symbol":"PEER","sector":"Tech"}]}',
            encoding="utf-8",
        )

        def up_detect(now):
            return [AnchorShock(anchor_symbol="ANC", as_of=now, prev_close=100.0,
                                close=108.0, move_pct=0.08)]

        summary = paper_run.run_paper_tick(
            universe_path="stockbot/data/universe_expanded.csv",
            db_path=tmp_path / "paper.db",
            state_dir=tmp_path,
            feed=_Feed(),
            quality_gate=lambda ev: None,
            frontier=True,
            frontier_up=True,
            frontier_graph_db=graph_db,
            universe_metadata=meta,
            anchor_shock_detector=lambda now: [],     # no DOWN shock
            anchor_up_shock_detector=up_detect,        # an UP shock today
            frontier_price_snapshot=lambda syms: {s.upper(): 10.0 for s in syms},
        )
        assert "frontier" in summary["lanes"] and "frontier_mech" in summary["lanes"]
        store = StateStore(tmp_path / "paper.db")
        up_sig = store.get_decisions("frontier", "paper", "signal")
        assert [d["action"] for d in up_sig] == ["anchor_up_shock_detected"]
        # Control twin mirrors the up side too.
        mech_sig = store.get_decisions("frontier_mech", "paper", "signal")
        assert [d["action"] for d in mech_sig] == ["anchor_up_shock_detected"]

    def test_up_side_off_without_up_detector(self, tmp_path: Path) -> None:
        from stockbot.harness import paper_run

        graph_db = _graph_with_edge(tmp_path)
        meta = tmp_path / "meta.json"
        meta.write_text(
            '{"symbols":[{"symbol":"FLR","sector":"Tech"},{"symbol":"PEER","sector":"Tech"}]}',
            encoding="utf-8",
        )
        summary = paper_run.run_paper_tick(
            universe_path="stockbot/data/universe_expanded.csv",
            db_path=tmp_path / "paper.db",
            state_dir=tmp_path,
            feed=_Feed(),
            quality_gate=lambda ev: None,
            frontier=True,
            frontier_up=True,  # on, but no up detector injected -> stays inert
            frontier_graph_db=graph_db,
            universe_metadata=meta,
            anchor_shock_detector=lambda now: [],
            frontier_price_snapshot=lambda syms: {},
        )
        store = StateStore(tmp_path / "paper.db")
        assert store.get_decisions("frontier", "paper", "signal") == []

"""Shot 8 — Frontier Engine propagation (3c/3d): shared semantics, the
PIT-correct sector-neutral backtest, the strategy lifecycle, and the
graph-vs-coin control twin.

All offline: synthetic price CSVs + injected anchor-shock detectors. No network,
no LLM (the propagation engine is mechanical by design).
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone

from pathlib import Path

import pytest

from stockbot.bots.frontier.propagation import (
    ANCHOR_SHOCK_THRESHOLD,
    DIRECTION_MAP,
    EDGE_MAX_AGE_DAYS,
    AnchorShock,
    FrontierConfig,
    FrontierPropagationStrategy,
    is_anchor_shock,
    pit_valid_tradeable_edges,
    predicted_direction,
    sector_benchmark_peers,
    sector_neutral_excess,
    sign_is_correct,
    strategy_bucket_for,
    trade_type_for,
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
# Shared semantics (the pieces the strategy AND the backtest both import)
# ---------------------------------------------------------------------------


class TestSharedSemantics:
    def test_direction_map_is_the_falsifiable_hypothesis(self) -> None:
        assert predicted_direction("customer_concentration") == "down"
        assert predicted_direction("sole_supplier") == "down"
        # regulatory_precedent is OUT of scope for price-anchor propagation.
        assert predicted_direction("regulatory_precedent") is None
        assert "regulatory_precedent" not in DIRECTION_MAP

    def test_anchor_shock_is_down_only_at_threshold(self) -> None:
        assert is_anchor_shock(100.0, 100.0 * (1 - ANCHOR_SHOCK_THRESHOLD))  # exactly -5%
        assert not is_anchor_shock(100.0, 96.0)  # -4% does not fire
        assert not is_anchor_shock(100.0, 110.0)  # up moves never fire (down-only)
        assert not is_anchor_shock(0.0, 50.0)  # bad prev close

    def test_sign_correct_for_down_prediction(self) -> None:
        assert sign_is_correct("down", -0.03) is True   # filer underperformed -> correct
        assert sign_is_correct("down", 0.03) is False
        assert sign_is_correct("up", 0.03) is True

    def test_sector_benchmark_peers_excludes_self_and_caps(self) -> None:
        members = [f"S{i:02d}" for i in range(60)] + ["FILER"]
        peers = sector_benchmark_peers("FILER", members, max_peers=40)
        assert "FILER" not in peers
        assert len(peers) == 40
        assert peers == sorted(peers)  # deterministic

    def test_sector_neutral_excess_subtracts_equal_weight_mean(self) -> None:
        assert sector_neutral_excess(0.05, [0.01, 0.03]) == pytest.approx(0.05 - 0.02)
        assert sector_neutral_excess(0.05, []) is None  # no peers -> undefined

    def test_pit_selection_honors_18mo_window_and_observability(self) -> None:
        as_of = datetime(2026, 3, 1, tzinfo=UTC)
        base = {
            "edge_type": "customer_concentration",
            "from_symbol": "FLR",
            "counterparty_symbol": "ANC",
        }
        edges = [
            {**base, "filed_at": "2026-02-01"},  # live
            {**base, "filed_at": "2026-04-01"},  # FUTURE filing -> excluded (no leak)
            {**base, "filed_at": "2024-06-01"},  # > 18mo old -> decayed out
            {**base, "counterparty_symbol": None, "filed_at": "2026-02-01"},  # unobservable
            {**base, "edge_type": "regulatory_precedent", "filed_at": "2026-02-01"},  # out of scope
        ]
        live = pit_valid_tradeable_edges(edges, as_of)
        assert len(live) == 1
        assert live[0]["filed_at"] == "2026-02-01"

    def test_pit_window_boundary_is_inclusive(self) -> None:
        as_of = datetime(2026, 3, 1, tzinfo=UTC)
        oldest_live = (as_of - timedelta(days=EDGE_MAX_AGE_DAYS)).date().isoformat()
        edge = {
            "edge_type": "sole_supplier",
            "from_symbol": "FLR",
            "counterparty_symbol": "ANC",
            "filed_at": oldest_live,
        }
        assert len(pit_valid_tradeable_edges([edge], as_of)) == 1
        too_old = {**edge, "filed_at": (as_of - timedelta(days=EDGE_MAX_AGE_DAYS + 1)).date().isoformat()}
        assert pit_valid_tradeable_edges([too_old], as_of) == []

    def test_bucket_and_trade_type_naming(self) -> None:
        assert strategy_bucket_for("customer_concentration") == "frontier:customer_concentration"
        assert strategy_bucket_for("sole_supplier") == "frontier:sole_supplier"
        assert trade_type_for("sole_supplier") == "frontier_sole_supplier"


# ---------------------------------------------------------------------------
# The PIT-correct, sector-neutral backtest (gate measurement)
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


class TestBacktest:
    def test_enumerate_signals_pit_and_sector_neutral_sign_accuracy(self, tmp_path: Path) -> None:
        d0 = date(2026, 2, 2)
        # Anchor ANC: flat then a -8% shock on day 5 (index 5).
        anchor = _daily(d0, [100, 100, 100, 100, 100, 92, 92, 92, 92, 92, 92, 92])
        # Filer FLR: flat to entry (day6), then falls 6% over the 5-day window.
        filer = _daily(d0, [50, 50, 50, 50, 50, 50, 50, 49.5, 49.0, 48.5, 48.0, 47.0])
        # Sector peers PEERA/PEERB: roughly flat (so the filer underperforms).
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
        signals = fb.enumerate_signals(edges, anchor_series)
        assert len(signals) == 1
        assert signals[0].filer == "FLR" and signals[0].direction == "down"

        symbol_sector = {"FLR": "Tech", "PEERA": "Tech", "PEERB": "Tech"}
        sector_members = {"Tech": ["FLR", "PEERA", "PEERB"]}
        uni_series = fb.load_price_series(uni_csv)
        resolved = fb.backtest_signals(signals, uni_series, symbol_sector, sector_members)
        assert len(resolved) == 1
        r = resolved[0]
        # Entry T+1 = day 6 (price 50), exit +5 trading days = day 11 (price 47): -6%.
        assert r.filer_return == pytest.approx((47.0 - 50.0) / 50.0)
        # Peers flat -> excess == filer return < 0 -> DOWN prediction is correct.
        assert r.sector_neutral is True
        assert r.excess_return == pytest.approx(-0.06)
        assert r.outcome is True

        buckets = fb.sign_accuracy_by_bucket(resolved)
        assert buckets["customer_concentration"]["n"] == 1
        assert buckets["customer_concentration"]["sign_accuracy"] == 1.0

    def test_future_filed_edge_never_leaks_into_a_past_shock(self, tmp_path: Path) -> None:
        d0 = date(2026, 2, 2)
        anchor = _daily(d0, [100, 90, 90, 90, 90, 90, 90, 90])  # shock on day 1
        anchor_csv = tmp_path / "a.csv"
        _write_prices(anchor_csv, {"ANC": anchor})
        # Edge filed AFTER the shock day -> must not produce a signal (no leak).
        edges = [{
            "edge_type": "customer_concentration", "from_symbol": "FLR",
            "counterparty_symbol": "ANC", "filed_at": "2026-03-01", "magnitude_pct": 30.0,
        }]
        signals = fb.enumerate_signals(edges, fb.load_price_series(anchor_csv, {"ANC"}))
        assert signals == []


# ---------------------------------------------------------------------------
# Strategy lifecycle (detect T -> enter T+1 -> resolve T+1+N) end-to-end
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


def _shock_detector_on(day_index: int, anchor: str, *, start: datetime = START):
    shock_date = (start + timedelta(days=day_index)).date()

    def detect(now: datetime) -> list[AnchorShock]:
        if now.date() != shock_date:
            return []
        return [AnchorShock(anchor_symbol=anchor, as_of=now, prev_close=100.0, close=92.0, move_pct=-0.08)]

    return detect


class TestStrategyLifecycle:
    def test_signal_enters_next_bar_and_resolves_sector_neutral(self, tmp_path: Path) -> None:
        # FLR drops 4% over the window while peers are flat -> DOWN prediction wins.
        closes = {
            "FLR":   [50, 50, 50, 50, 50, 49.5, 49.0, 48.5, 48.0, 48.0, 48.0],
            "PEERA": [10] * 11,
            "PEERB": [20] * 11,
        }
        data_dir = tmp_path / "data"
        _write_feed_dataset(data_dir, closes)
        db_path = tmp_path / "state.db"
        cfg = _frontier_cfg(data_dir, db_path)

        edges = [{
            "edge_type": "sole_supplier", "from_symbol": "FLR",
            "counterparty_symbol": "ANC", "filed_at": "2026-01-01", "magnitude_pct": None,
        }]

        def factory(_comp) -> Strategy:
            return FrontierPropagationStrategy(
                FrontierConfig(hold_trading_days=5),
                anchor_shock_detector=_shock_detector_on(2, "ANC"),
                sector_members={"FLR": ["PEERA", "PEERB"]},
                edges=edges,
            )

        drive_strategy(factory, cfg)
        store = StateStore(db_path)

        signals = store.get_decisions("frontier", "backtest", "signal")
        assert [d["action"] for d in signals] == ["anchor_shock_detected"]
        entries = store.get_decisions("frontier", "backtest", "entry")
        assert [d["action"] for d in entries] == ["prediction_opened"]
        resolutions = store.get_decisions("frontier", "backtest", "resolution")
        assert len(resolutions) == 1
        res = resolutions[0]
        assert res["action"] == "sign_correct"
        assert res["inputs"]["sector_neutral"] is True
        assert res["inputs"]["strategy_bucket"] == "frontier:sole_supplier"

        with store.transaction() as conn:
            cal = conn.execute(
                "SELECT strategy_bucket, outcome, stated_probability FROM calibration_ledger"
            ).fetchall()
        assert len(cal) == 1
        assert cal[0]["strategy_bucket"] == "frontier:sole_supplier"
        assert cal[0]["outcome"] == 1
        assert cal[0]["stated_probability"] == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# Graph-vs-coin control twin: the concurrency cap is the only mechanical
# selection, and it bites when one anchor links several filers.
# ---------------------------------------------------------------------------


class TestControlTwin:
    def test_graph_lane_caps_concurrency_mech_takes_every_linked_name(self, tmp_path: Path) -> None:
        # One anchor ANC links 3 filers. Graph lane (cap=2) opens 2 and skips 1;
        # the mech twin (no cap) opens all 3.
        filers = ["FA", "FB", "FC"]
        closes = {f: [50, 50, 50, 49, 48, 47, 46, 46] for f in filers}
        closes["PEER"] = [10] * 8
        data_dir = tmp_path / "data"
        _write_feed_dataset(data_dir, closes)
        db_path = tmp_path / "state.db"
        cfg = _frontier_cfg(data_dir, db_path)

        edges = [
            {"edge_type": "customer_concentration", "from_symbol": f,
             "counterparty_symbol": "ANC", "filed_at": "2026-01-01", "magnitude_pct": 30.0}
            for f in filers
        ]
        sector_members = {f: ["PEER"] for f in filers}
        detector = _shock_detector_on(2, "ANC")

        def graph_factory(_comp) -> Strategy:
            return FrontierPropagationStrategy(
                FrontierConfig(hold_trading_days=3, max_concurrent=2, apply_concurrency_cap=True),
                bot_id="frontier", anchor_shock_detector=detector,
                sector_members=sector_members, edges=edges,
            )

        def mech_factory(_comp) -> Strategy:
            return FrontierPropagationStrategy(
                FrontierConfig(hold_trading_days=3, apply_concurrency_cap=False),
                bot_id="frontier_mech", anchor_shock_detector=detector,
                sector_members=sector_members, edges=edges,
            )

        drive_strategies([graph_factory, mech_factory], cfg)
        store = StateStore(db_path)

        graph_open = [d for d in store.get_decisions("frontier", "backtest", "entry")
                      if d["action"] == "prediction_opened"]
        graph_skipped = [d for d in store.get_decisions("frontier", "backtest", "entry")
                         if d["action"] == "skipped_concurrency_cap"]
        mech_open = [d for d in store.get_decisions("frontier_mech", "backtest", "entry")
                     if d["action"] == "prediction_opened"]
        assert len(graph_open) == 2
        assert len(graph_skipped) == 1
        assert len(mech_open) == 3


# ---------------------------------------------------------------------------
# Paper lanes are additive + config-gated (default OFF -> existing lanes intact)
# ---------------------------------------------------------------------------


class TestPaperLaneWiring:
    def test_frontier_lanes_added_when_enabled(self, tmp_path: Path, monkeypatch) -> None:
        from stockbot.harness import paper_run

        # A tiny graph DB with one observable tradeable edge.
        graph_db = tmp_path / "graph.db"
        gstore = StateStore(graph_db)
        gstore.record_frontier_edge(
            edge_id="e1", edge_type="customer_concentration", from_symbol="FLR",
            counterparty_name="Anchor Inc", counterparty_symbol="ANC", named=True,
            magnitude_pct=30.0, quoted_passage="q", accession_number="a",
            filing_url="u", form="10-K", filed_at="2026-01-01",
            extracted_at=datetime(2026, 1, 2, tzinfo=UTC), model_id="m",
        )
        meta = tmp_path / "meta.json"
        meta.write_text('{"symbols":[{"symbol":"FLR","sector":"Tech"},{"symbol":"PEER","sector":"Tech"}]}', encoding="utf-8")

        # Stub feed: one tick, no events, quotes available for FLR/PEER/ANC.
        class _Feed:
            def __init__(self) -> None:
                self._t = datetime(2026, 2, 2, 20, tzinfo=UTC)
                self._adv = False
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

        # No shock today -> lanes run but open nothing; this exercises the wiring.
        summary = paper_run.run_paper_tick(
            universe_path="stockbot/data/universe_expanded.csv",
            db_path=tmp_path / "paper.db",
            state_dir=tmp_path,
            feed=_Feed(),
            quality_gate=lambda ev: None,
            frontier=True,
            frontier_graph_db=graph_db,
            universe_metadata=meta,
            anchor_shock_detector=lambda now: [],
            frontier_price_snapshot=lambda syms: {},
        )
        assert "frontier" in summary["lanes"]
        assert "frontier_mech" in summary["lanes"]
        # The existing two lanes are still present (additive, not replaced).
        assert "proportionality" in summary["lanes"]
        assert "proportionality_mech" in summary["lanes"]

"""Dashboard v3 Phase 1 — strategy-agnostic data layer tests."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.core.state.store import StateStore
from stockbot.types import PortfolioSnapshot
from stockbot.dashboard import data as d
from stockbot.dashboard import ops as o

UTC = timezone.utc
T0 = datetime(2026, 6, 1, 21, 0, tzinfo=UTC)

# Registry bot_ids (must match data.py _LANE_REGISTRY — not used in query bodies here).
BOT_RIDE_BRAIN = "proportionality"
BOT_RIDE_MECH = "proportionality_mech"
BOT_FADE_V2 = "crash_fade_v2"
BOT_FADE_MECH = "crash_fade_mech"
BOT_FRONTIER = "frontier"
BOT_FRONTIER_MECH = "frontier_mech"
BOT_SHADOW = "proportionality_shadow"


def _seed_multi_lane_db(db_path: Path) -> StateStore:
    """Paper DB with ride, fade (3-way), and frontier lanes."""
    store = StateStore(db_path)
    store.start_run(
        run_id="run-dashv3",
        bot_id=BOT_RIDE_BRAIN,
        mode="paper",
        config_hash="cafe",
        model_versions={},
        log_path="logs/run.log",
        started_at=T0,
    )
    store.finish_run("run-dashv3", status="completed", finished_at=T0 + timedelta(minutes=5))

    # --- earnings ride resolution (pairwise race) ---
    store.record_thesis(
        thesis_id="t-ride",
        bot_id=BOT_RIDE_BRAIN,
        symbol="WIN",
        direction="long",
        stated_probability=0.72,
        mode="paper",
        strategy_bucket="earnings:ride",
        created_at=T0,
    )
    store.record_decision(
        decision_id="res-ride-g",
        bot_id=BOT_RIDE_BRAIN,
        decision_type="resolution",
        action="win",
        inputs={"event_id": "e-ride", "symbol": "WIN", "net_return": 0.10, "strategy_bucket": "earnings:ride"},
        mode="paper",
        as_of=T0 + timedelta(days=10),
        thesis_id="t-ride",
    )
    store.record_decision(
        decision_id="res-ride-m",
        bot_id=BOT_RIDE_MECH,
        decision_type="resolution",
        action="win",
        inputs={"event_id": "e-ride", "symbol": "WIN", "net_return": 0.08, "strategy_bucket": "earnings:ride"},
        mode="paper",
        as_of=T0 + timedelta(days=10),
    )
    store.record_calibration(
        thesis_id="t-ride",
        bot_id=BOT_RIDE_BRAIN,
        trade_type="earnings_ride",
        stated_probability=0.72,
        outcome=True,
        bucket="medium_confidence",
        strategy_bucket="earnings:ride",
        resolved_at=T0 + timedelta(days=10),
    )

    # --- fade 3-way race on one event ---
    for bot, key, net, bucket in (
        (BOT_RIDE_BRAIN, "v1", 0.06, "crash:fade"),
        (BOT_FADE_V2, "v2", 0.04, "crash:fade_v2"),
        (BOT_FADE_MECH, "coin", 0.02, "crash:fade"),
    ):
        tid = f"t-fade-{key}"
        store.record_thesis(
            thesis_id=tid,
            bot_id=bot,
            symbol="CRSH",
            direction="long",
            stated_probability=0.58,
            mode="paper",
            strategy_bucket=bucket,
            created_at=T0 + timedelta(days=1),
        )
        store.record_decision(
            decision_id=f"res-fade-{key}",
            bot_id=bot,
            decision_type="resolution",
            action="win",
            inputs={
                "event_id": "e-crash-1",
                "symbol": "CRSH",
                "net_return": net,
                "strategy_bucket": bucket,
                "trade_kind": "fade",
            },
            mode="paper",
            as_of=T0 + timedelta(days=12),
            thesis_id=tid,
        )

    store.record_decision(
        decision_id="fade-signal-1",
        bot_id=BOT_RIDE_BRAIN,
        decision_type="entry",
        action="buy",
        inputs={"symbol": "CRSH", "fade_gate": {"vetoed": False, "flags": []}},
        mode="paper",
        as_of=T0 + timedelta(days=1),
    )

    # --- frontier up calibration ---
    store.record_thesis(
        thesis_id="t-front-up",
        bot_id=BOT_FRONTIER,
        symbol="MQ",
        direction="long",
        stated_probability=0.55,
        mode="paper",
        strategy_bucket="frontier:customer_concentration_up",
        created_at=T0 + timedelta(days=2),
    )
    store.record_calibration(
        thesis_id="t-front-up",
        bot_id=BOT_FRONTIER,
        trade_type="frontier_customer_concentration_up",
        stated_probability=0.55,
        outcome=True,
        bucket="medium_confidence",
        strategy_bucket="frontier:customer_concentration_up",
        resolved_at=T0 + timedelta(days=15),
    )

    # --- shadow lane presence ---
    store.record_snapshot(
        __import__("stockbot.types", fromlist=["PortfolioSnapshot"]).PortfolioSnapshot(
            cash=100_000.0, positions=[], total_equity=100_000.0, as_of=T0
        ),
        bot_id=BOT_SHADOW,
        mode="paper",
    )

    return store


def _seed_rh_closed_trade(db_path: Path) -> None:
    """Known RH round trip on the mech lane: −$314.66 realized P/L."""
    store = StateStore(db_path)
    store.record_thesis(
        thesis_id="t-rh",
        bot_id=BOT_RIDE_MECH,
        symbol="RH",
        direction="long",
        stated_probability=0.55,
        mode="paper",
        strategy_bucket="earnings:ride",
        created_at=T0,
    )
    buy_at = T0 + timedelta(days=1)
    sell_at = T0 + timedelta(days=8)
    store.record_fill(
        fill_id="f-rh-buy",
        order_id="o-rh-buy",
        symbol="RH",
        side="buy",
        quantity=25.1067,
        price=159.38394220586738,
        slippage_pct=0.002,
        bot_id=BOT_RIDE_MECH,
        mode="paper",
        filled_at=buy_at,
        thesis_id="t-rh",
    )
    store.record_fill(
        fill_id="f-rh-sell",
        order_id="o-rh-sell",
        symbol="RH",
        side="sell",
        quantity=25.1067,
        price=146.85103847938754,
        slippage_pct=0.002,
        bot_id=BOT_RIDE_MECH,
        mode="paper",
        filled_at=sell_at,
        thesis_id="t-rh",
    )
    store.record_decision(
        decision_id="d-rh-exit",
        bot_id=BOT_RIDE_MECH,
        decision_type="exit",
        action="trailing_stop",
        inputs={"symbol": "RH", "thesis_id": "t-rh"},
        mode="paper",
        as_of=sell_at,
        thesis_id="t-rh",
    )


def _seed_open_and_multilot(db_path: Path) -> None:
    store = StateStore(db_path)
    # Still-open: buy only
    store.record_thesis(
        thesis_id="t-open",
        bot_id=BOT_RIDE_MECH,
        symbol="OPEN",
        direction="long",
        stated_probability=0.55,
        mode="paper",
        strategy_bucket="earnings:ride",
        created_at=T0,
    )
    store.record_fill(
        fill_id="f-open-buy",
        order_id="o-open-buy",
        symbol="OPEN",
        side="buy",
        quantity=10,
        price=100.0,
        slippage_pct=0.002,
        bot_id=BOT_RIDE_MECH,
        mode="paper",
        filled_at=T0 + timedelta(days=1),
        thesis_id="t-open",
    )
    # Multi-lot FIFO on XYZ
    store.record_thesis(
        thesis_id="t-xyz",
        bot_id=BOT_RIDE_MECH,
        symbol="XYZ",
        direction="long",
        stated_probability=0.55,
        mode="paper",
        strategy_bucket="earnings:ride",
        created_at=T0,
    )
    for i, (qty, price) in enumerate(((50, 100.0), (50, 110.0)), start=1):
        store.record_fill(
            fill_id=f"f-xyz-buy-{i}",
            order_id=f"o-xyz-buy-{i}",
            symbol="XYZ",
            side="buy",
            quantity=qty,
            price=price,
            slippage_pct=0.002,
            bot_id=BOT_RIDE_MECH,
            mode="paper",
            filled_at=T0 + timedelta(days=i),
            thesis_id="t-xyz",
        )
    store.record_fill(
        fill_id="f-xyz-sell",
        order_id="o-xyz-sell",
        symbol="XYZ",
        side="sell",
        quantity=100,
        price=105.0,
        slippage_pct=0.002,
        bot_id=BOT_RIDE_MECH,
        mode="paper",
        filled_at=T0 + timedelta(days=5),
        thesis_id="t-xyz",
    )


def _record_closed_round_trip(
    store: StateStore,
    *,
    bot_id: str,
    symbol: str,
    bucket: str,
    realized_pl: float,
    base: datetime,
    tag: str,
) -> None:
    """Buy/sell fills with qty=100 @ $100 entry; exit price set to hit ``realized_pl``."""
    qty = 100.0
    entry = 100.0
    exit_price = entry + realized_pl / qty
    tid = f"t-{tag}"
    buy_at = base + timedelta(hours=1)
    sell_at = base + timedelta(days=5)
    store.record_thesis(
        thesis_id=tid,
        bot_id=bot_id,
        symbol=symbol,
        direction="long",
        stated_probability=0.55,
        mode="paper",
        strategy_bucket=bucket,
        created_at=base,
    )
    store.record_fill(
        fill_id=f"f-{tag}-buy",
        order_id=f"o-{tag}-buy",
        symbol=symbol,
        side="buy",
        quantity=qty,
        price=entry,
        slippage_pct=0.002,
        bot_id=bot_id,
        mode="paper",
        filled_at=buy_at,
        thesis_id=tid,
    )
    store.record_fill(
        fill_id=f"f-{tag}-sell",
        order_id=f"o-{tag}-sell",
        symbol=symbol,
        side="sell",
        quantity=qty,
        price=exit_price,
        slippage_pct=0.002,
        bot_id=bot_id,
        mode="paper",
        filled_at=sell_at,
        thesis_id=tid,
    )


def _seed_proportionality_bucket_standing(db_path: Path) -> None:
    """Commingled proportionality book: winning ride + losing fade on the brain lane."""
    store = StateStore(db_path)
    base = T0 + timedelta(days=20)
    # Brain lane — both buckets on proportionality
    _record_closed_round_trip(
        store,
        bot_id=BOT_RIDE_BRAIN,
        symbol="RIDEWIN",
        bucket="earnings:ride",
        realized_pl=1000.0,
        base=base,
        tag="ride-brain",
    )
    _record_closed_round_trip(
        store,
        bot_id=BOT_RIDE_BRAIN,
        symbol="FADELOSS",
        bucket="crash:fade",
        realized_pl=-500.0,
        base=base + timedelta(days=1),
        tag="fade-brain",
    )
    # Control lanes — one trade each in their strategy bucket
    _record_closed_round_trip(
        store,
        bot_id=BOT_RIDE_MECH,
        symbol="RIDECOIN",
        bucket="earnings:ride",
        realized_pl=200.0,
        base=base + timedelta(days=2),
        tag="ride-mech",
    )
    _record_closed_round_trip(
        store,
        bot_id=BOT_FADE_MECH,
        symbol="FADECOIN",
        bucket="crash:fade",
        realized_pl=-100.0,
        base=base + timedelta(days=3),
        tag="fade-mech",
    )


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_empty_db_lists_all_strategies_and_lanes(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.db"
        StateStore(db)
        strats = d.strategies(db)
        scoreboard_strats = [s for s in strats if s["scoreboard"]]
        assert len(scoreboard_strats) == 4
        lane_ids = {lane["bot_id"] for s in strats for lane in s["lanes"]}
        assert lane_ids == {
            BOT_RIDE_BRAIN,
            BOT_RIDE_MECH,
            BOT_FADE_V2,
            BOT_FADE_MECH,
            BOT_FRONTIER,
            BOT_FRONTIER_MECH,
            BOT_SHADOW,
        }

    def test_populated_db_marks_active_lanes(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_multi_lane_db(db)
        strats = {s["strategy_id"]: s for s in d.strategies(db)}
        assert strats["crash_fade"]["lanes"][0]["active"] is True
        assert strats["frontier_up"]["lanes"][0]["active"] is True
        assert strats["shadow_rehearsal"]["lanes"][0]["active"] is True

    def test_lane_roles_and_tradeability(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        strats = {s["strategy_id"]: s for s in d.strategies(db)}
        fade = {lane["bot_id"]: lane for lane in strats["crash_fade"]["lanes"]}
        assert fade[BOT_RIDE_BRAIN]["role"] == "brain"
        assert fade[BOT_FADE_V2]["role"] == "brain_v2"
        assert fade[BOT_FADE_MECH]["role"] == "control"
        down = strats["frontier_down"]["lanes"][0]
        assert down["predict_only"] is True
        assert down["tradeable"] is False

    def test_engines_grouping(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        engines = d.engines(db)
        assert [e["engine_id"] for e in engines] == ["proportionality", "frontier"]
        prop = next(e for e in engines if e["engine_id"] == "proportionality")
        assert len(prop["strategies"]) == 2


# ---------------------------------------------------------------------------
# closed trades / FIFO P/L
# ---------------------------------------------------------------------------


class TestClosedTrades:
    def test_rh_round_trip_pl(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_rh_closed_trade(db)
        trades = d.closed_trades(db, strategy_id="earnings_ride")
        rh = next(t for t in trades if t["symbol"] == "RH")
        assert rh["bot_id"] == BOT_RIDE_MECH
        assert rh["entry_price"] == pytest.approx(159.3839, abs=1e-3)
        assert rh["exit_price"] == pytest.approx(146.8510, abs=1e-3)
        assert rh["quantity"] == pytest.approx(25.1067)
        assert rh["realized_pl"] == pytest.approx(-314.66, abs=0.01)
        assert rh["exit_reason"] == "trailing_stop"

    def test_frontier_resolution_reads_as_horizon_not_unknown(self, tmp_path: Path) -> None:
        """Frontier unwinds at its holding horizon via a 'resolution' decision,
        not an 'exit' one — the exit-reason lookup must still label it."""
        db = tmp_path / "paper.db"
        store = StateStore(db)
        tid = "fr-thesis-1"
        sell_at = T0 + timedelta(days=7)
        store.record_thesis(
            thesis_id=tid,
            bot_id="frontier",
            symbol="FILR",
            direction="long",
            stated_probability=0.55,
            mode="paper",
            strategy_bucket="frontier:sole_supplier_up",
            created_at=T0,
        )
        for tag, side, price, ts in (
            ("buy", "buy", 10.0, T0),
            ("sell", "sell", 11.0, sell_at),
        ):
            store.record_fill(
                fill_id=f"f-fr-{tag}",
                order_id=f"o-fr-{tag}",
                symbol="FILR",
                side=side,
                quantity=100.0,
                price=price,
                slippage_pct=0.002,
                bot_id="frontier",
                mode="paper",
                filled_at=ts,
                thesis_id=tid,
            )
        store.record_decision(
            decision_id="fr-res-1",
            bot_id="frontier",
            decision_type="resolution",
            action="sign_correct_up",
            inputs={"strategy_bucket": "frontier:sole_supplier_up"},
            as_of=sell_at,
            mode="paper",
            thesis_id=tid,
        )
        trade = next(t for t in d.closed_trades(db) if t["symbol"] == "FILR")
        assert trade["exit_reason"] == "horizon"

    def test_excludes_open_positions(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_open_and_multilot(db)
        trades = d.closed_trades(db, strategy_id="earnings_ride")
        assert all(t["symbol"] != "OPEN" for t in trades)

    def test_fifo_multilot(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_open_and_multilot(db)
        trades = d.closed_trades(db, strategy_id="earnings_ride")
        xyz = [t for t in trades if t["symbol"] == "XYZ"]
        assert len(xyz) == 2
        pls = sorted(t["realized_pl"] for t in xyz)
        assert pls == pytest.approx([-250.0, 250.0], abs=0.01)

    def test_global_execution_includes_all_strategies(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_rh_closed_trade(db)
        all_trades = d.closed_trades(db)
        assert any(t["symbol"] == "RH" for t in all_trades)


# ---------------------------------------------------------------------------
# race
# ---------------------------------------------------------------------------


class TestRace:
    def test_pairwise_ride(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_multi_lane_db(db)
        r = d.race(db, "earnings_ride")
        assert r["race_kind"] == "pairwise"
        assert r["summary"]["gated"]["resolved_trades"] == 1
        assert r["events"][0]["gated"] == pytest.approx(0.10)

    def test_three_way_fade(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_multi_lane_db(db)
        r = d.race(db, "crash_fade")
        assert r["race_kind"] == "three_way"
        assert set(r["summary"]) == {"v1", "v2", "coin"}
        ev = r["events"][0]
        assert ev["v1"] == pytest.approx(0.06)
        assert ev["v2"] == pytest.approx(0.04)
        assert ev["coin"] == pytest.approx(0.02)
        assert ev["cum_v1"] == pytest.approx(0.06)

    def test_empty_db_race_shape(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.db"
        StateStore(db)
        r = d.race(db, "crash_fade")
        assert r["events"] == []
        assert r["summary"]["v1"]["resolved_trades"] == 0


# ---------------------------------------------------------------------------
# scoreboard
# ---------------------------------------------------------------------------


class TestScoreboard:
    def test_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.db"
        StateStore(db)
        sb = d.scoreboard(db)
        ride = next(c for c in sb["strategies"] if c["strategy_id"] == "earnings_ride")
        assert ride["status"] == "too_early"
        assert ride["sample_n"] == 0
        assert ride["gate"]["value"] is None

    def test_populated_ride_measuring(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_multi_lane_db(db)
        sb = d.scoreboard(db)
        ride = next(c for c in sb["strategies"] if c["strategy_id"] == "earnings_ride")
        assert ride["sample_n"] == 1
        assert ride["status"] == "measuring"

    def test_fade_signals_count(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_multi_lane_db(db)
        sb = d.scoreboard(db)
        fade = next(c for c in sb["strategies"] if c["strategy_id"] == "crash_fade")
        # 1 fade-gate evaluation, but no fade has RESOLVED -> sample is 0, not 1.
        assert fade["gate"]["evaluated"] == 1
        assert fade["gate"]["sample_n"] == 0

    def test_fade_not_failing_when_many_evaluations_but_none_resolved(self, tmp_path: Path) -> None:
        # The user-hit bug: lots of crashes evaluated (>= the 40 bar) but zero
        # fades resolved must read 'too_early', never 'failing' — you can't fail
        # a falsifier with no resolved sample.
        db = tmp_path / "paper.db"
        store = StateStore(db)
        for i in range(50):
            store.record_decision(
                decision_id=f"fade-eval-{i}",
                bot_id=BOT_RIDE_BRAIN,
                decision_type="entry",
                action="skipped_fade_gate",
                inputs={"symbol": f"C{i}", "fade_gate": {"vetoed": False, "flags": []}},
                mode="paper",
                as_of=T0 + timedelta(days=1),
            )
        fade = next(c for c in d.scoreboard(db)["strategies"] if c["strategy_id"] == "crash_fade")
        assert fade["gate"]["evaluated"] == 50
        assert fade["gate"]["sample_n"] == 0
        assert fade["status"] == "too_early"

    def test_per_bucket_standing_ride_vs_fade(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_proportionality_bucket_standing(db)
        sb = d.scoreboard(db)
        ride = next(c for c in sb["strategies"] if c["strategy_id"] == "earnings_ride")
        fade = next(c for c in sb["strategies"] if c["strategy_id"] == "crash_fade")
        ride_st = ride["standing"]
        fade_st = fade["standing"]
        assert ride_st["basis"] == "realized_pl"
        assert fade_st["basis"] == "realized_pl"
        assert ride_st["brain_pl"] == pytest.approx(1000.0)
        assert ride_st["control_pl"] == pytest.approx(200.0)
        assert ride_st["edge"] == pytest.approx(800.0)
        assert fade_st["brain_pl"] == pytest.approx(-500.0)
        assert fade_st["control_pl"] == pytest.approx(-100.0)
        assert fade_st["edge"] == pytest.approx(-400.0)
        assert ride_st["edge"] != fade_st["edge"]


# ---------------------------------------------------------------------------
# strategy_detail
# ---------------------------------------------------------------------------


class TestStrategyDetail:
    def test_fade_includes_debates(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_multi_lane_db(db)
        detail = d.strategy_detail(db, "crash_fade")
        assert detail["strategy_id"] == "crash_fade"
        assert len(detail["lanes"]) == 3
        assert detail["race"]["race_kind"] == "three_way"
        assert isinstance(detail["debates"], list)

    def test_empty_db_detail(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.db"
        StateStore(db)
        detail = d.strategy_detail(db, "earnings_ride")
        assert detail["closed_trades"] == []
        assert detail["calibration_summary"]["n"] == 0

    def test_shared_lane_positions_partition_by_trade_kind(self, tmp_path: Path) -> None:
        """proportionality trades ride AND fade — the ride page must not show
        its fade position and vice versa (the owner's lumping concern)."""
        from stockbot.types import PortfolioSnapshot, Position

        db = tmp_path / "paper.db"
        store = StateStore(db)
        snap = PortfolioSnapshot(
            cash=90_000.0,
            positions=[
                Position(symbol="RIDEPOS", quantity=10, avg_cost=100.0,
                         market_value=1_000.0, unrealized_pnl=0.0),
                Position(symbol="FADEPOS", quantity=5, avg_cost=50.0,
                         market_value=250.0, unrealized_pnl=0.0),
            ],
            total_equity=91_250.0,
            as_of=T0,
        )
        store.record_snapshot(snap, bot_id=BOT_RIDE_BRAIN, mode="paper")
        sidecar = {
            BOT_RIDE_BRAIN: {"strategy": {"open": [
                {"symbol": "RIDEPOS", "trade_kind": "ride"},
                {"symbol": "FADEPOS", "trade_kind": "fade"},
            ]}}
        }
        ride = d.strategy_detail(db, "earnings_ride", sidecar=sidecar)
        fade = d.strategy_detail(db, "crash_fade", sidecar=sidecar)
        ride_syms = {p["symbol"] for p in ride["open_positions"][BOT_RIDE_BRAIN]["positions"]}
        fade_syms = {p["symbol"] for p in fade["open_positions"][BOT_RIDE_BRAIN]["positions"]}
        assert ride_syms == {"RIDEPOS"}
        assert fade_syms == {"FADEPOS"}

    def test_shared_lane_race_carries_curves_note(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_multi_lane_db(db)
        for sid in ("earnings_ride", "crash_fade"):
            note = d.race(db, sid).get("curves_note", "")
            assert "FULL book" in note and "proportionality" in note
        # frontier lanes aren't shared with another strategy's DIFFERENT book —
        # frontier_up/_down share bot_ids with each other, so they get one too.
        assert "curves_note" in d.race(db, "frontier_up")


# ---------------------------------------------------------------------------
# crumbs ran-vs-empty
# ---------------------------------------------------------------------------


class TestCrumbsFeedStatus:
    def test_absent(self, tmp_path: Path) -> None:
        status = o.crumbs_feed_status(tmp_path / "missing.json")
        assert status["state"] == "absent"

    def test_ran_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "crumbs_alerts.json"
        path.write_text("[]", encoding="utf-8")
        now = datetime.now(UTC)
        os.utime(path, (now.timestamp(), now.timestamp()))
        status = o.crumbs_feed_status(path, now=now)
        assert status["state"] == "ran_empty"
        assert status["alert_count"] == 0

    def test_ran_with_alerts(self, tmp_path: Path) -> None:
        path = tmp_path / "crumbs_alerts.json"
        path.write_text(
            json.dumps([{"company": "Acme", "filed_at": "2026-06-10"}]),
            encoding="utf-8",
        )
        now = datetime.now(UTC)
        os.utime(path, (now.timestamp(), now.timestamp()))
        status = o.crumbs_feed_status(path, now=now)
        assert status["state"] == "ran_with_alerts"
        assert status["alert_count"] == 1

    def test_stale(self, tmp_path: Path) -> None:
        path = tmp_path / "crumbs_alerts.json"
        path.write_text("[]", encoding="utf-8")
        old = datetime.now(UTC) - timedelta(hours=48)
        os.utime(path, (old.timestamp(), old.timestamp()))
        status = o.crumbs_feed_status(path, now=datetime.now(UTC), stale_hours=36)
        assert status["state"] == "stale"


# ---------------------------------------------------------------------------
# paper tick DB reconciliation
# ---------------------------------------------------------------------------


class TestPaperTickCompletedToday:
    def test_completed_run_on_day(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        day = date(2026, 6, 18)
        finished = datetime(2026, 6, 18, 22, 0, tzinfo=UTC)  # 5 PM CDT
        store.start_run(
            run_id="run-today",
            bot_id=BOT_RIDE_BRAIN,
            mode="paper",
            config_hash="cafe",
            model_versions={},
            log_path="logs/run.log",
            started_at=finished - timedelta(minutes=5),
        )
        store.finish_run("run-today", status="completed", finished_at=finished)
        # A real tick also writes a snapshot; a run row alone is not proof.
        store.record_snapshot(
            PortfolioSnapshot(cash=1.0, positions=[], total_equity=1.0, as_of=finished),
            bot_id=BOT_RIDE_BRAIN,
            mode="paper",
        )
        assert d.paper_tick_completed_today(db, today=day) is True

    def test_completed_run_without_snapshot_is_not_a_tick(self, tmp_path: Path) -> None:
        """A crashed tick still wrote 'completed' run rows (loop.py finally) —
        the 2026-08 outage that showed green ops for 17 dead days."""
        db = tmp_path / "paper.db"
        store = StateStore(db)
        day = date(2026, 6, 18)
        finished = datetime(2026, 6, 18, 22, 0, tzinfo=UTC)
        store.start_run(
            run_id="run-crashed",
            bot_id=BOT_RIDE_BRAIN,
            mode="paper",
            config_hash="cafe",
            model_versions={},
            log_path="logs/run.log",
            started_at=finished - timedelta(minutes=5),
        )
        store.finish_run("run-crashed", status="completed", finished_at=finished)
        assert d.paper_tick_completed_today(db, today=day) is False

    def test_failed_run_not_counted(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        day = date(2026, 6, 18)
        finished = datetime(2026, 6, 18, 22, 0, tzinfo=UTC)
        store.start_run(
            run_id="run-fail",
            bot_id=BOT_RIDE_BRAIN,
            mode="paper",
            config_hash="cafe",
            model_versions={},
            log_path="logs/run.log",
            started_at=finished - timedelta(minutes=5),
        )
        store.finish_run("run-fail", status="failed", finished_at=finished)
        assert d.paper_tick_completed_today(db, today=day) is False

    def test_yesterday_run_not_today(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        finished = datetime(2026, 6, 17, 22, 0, tzinfo=UTC)
        store.start_run(
            run_id="run-yday",
            bot_id=BOT_RIDE_BRAIN,
            mode="paper",
            config_hash="cafe",
            model_versions={},
            log_path="logs/run.log",
            started_at=finished - timedelta(minutes=5),
        )
        store.finish_run("run-yday", status="completed", finished_at=finished)
        assert d.paper_tick_completed_today(db, today=date(2026, 6, 18)) is False

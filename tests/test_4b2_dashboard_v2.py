"""Ticket 4b-2 — dashboard v2 (the watching-the-race view).

All offline: a seeded temp paper DB, fake ops logs (mixed UTF-8/UTF-16LE, as
the real scheduled tasks produce), a fake 2d sidecar, and a crumbs alert feed.
The dashboard itself must stay read-only — these tests only verify queries
and rendering, never writes from dashboard code.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stockbot.core.state.store import StateStore
from stockbot.dashboard import data as d
from stockbot.dashboard import ops as o
from stockbot.dashboard.app import render_page, render_strategy_page, render_thesis_page
from stockbot.types import PortfolioSnapshot, Position

UTC = timezone.utc
T0 = datetime(2026, 6, 1, 21, 0, tzinfo=UTC)

GATED = "proportionality"
MECH = "proportionality_mech"


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------


def _seed_paper_db(db_path: Path) -> StateStore:
    store = StateStore(db_path)

    store.start_run(
        run_id="run-0001-abcdef",
        bot_id=GATED,
        mode="paper",
        config_hash="cafe",
        model_versions={},
        log_path="logs/run.log",
        started_at=T0,
    )
    store.finish_run("run-0001-abcdef", status="completed", finished_at=T0 + timedelta(minutes=5))

    # --- resolved round trip on the gated lane (event e-1) ---
    store.record_thesis(
        thesis_id="t-closed",
        bot_id=GATED,
        symbol="WIN",
        direction="long",
        stated_probability=0.72,
        mode="paper",
        rationale="Mechanical ride entry: surprise +0.2000 passed top_decile | quality: durable demand [ensemble_disagreement]",
        created_at=T0,
        strategy_bucket="earnings:ride",
    )
    store.record_decision(
        decision_id="dec-entry-closed",
        bot_id=GATED,
        decision_type="entry",
        action="buy",
        inputs={
            "event_id": "e-1",
            "symbol": "WIN",
            "surprise_pct": 0.20,
            "quality": {
                "scored": True,
                "quality_score": 0.81,
                "stated_probability": 0.72,
                "flags": ["ensemble_disagreement"],
                "triage": {"worth_scoring": True, "reason": "real results"},
            },
            "stated_probability": 0.72,
        },
        mode="paper",
        as_of=T0,
        run_id="run-0001-abcdef",
        thesis_id="t-closed",
        model_id="claude-opus-4-8",
        prompt_version="qscore-v1",
    )
    store.record_fill(
        fill_id="f-entry-closed", order_id="o-entry-closed", symbol="WIN", side="buy",
        quantity=100, price=10.0, slippage_pct=0.002, bot_id=GATED, mode="paper",
        filled_at=T0 + timedelta(days=1),
    )
    store.record_fill(
        fill_id="f-exit-closed", order_id="o-exit-closed", symbol="WIN", side="sell",
        quantity=100, price=11.0, slippage_pct=0.002, bot_id=GATED, mode="paper",
        filled_at=T0 + timedelta(days=9),
    )
    store.resolve_thesis("t-closed", outcome=True, resolved_at=T0 + timedelta(days=10))
    store.record_decision(
        decision_id="dec-resolution-closed",
        bot_id=GATED,
        decision_type="resolution",
        action="win",
        inputs={"event_id": "e-1", "symbol": "WIN", "net_return": 0.10, "strategy_bucket": "earnings:ride"},
        mode="paper",
        as_of=T0 + timedelta(days=10),
        thesis_id="t-closed",
    )

    # --- the mech twin traded e-1 AND e-2 (e-2 was gate-vetoed on gated) ---
    for event_id, sym, net, when in (
        ("e-1", "WIN", 0.08, 10),
        ("e-2", "VETO", -0.04, 12),
    ):
        thesis_id = f"t-mech-{event_id}"
        store.record_thesis(
            thesis_id=thesis_id, bot_id=MECH, symbol=sym, direction="long",
            stated_probability=0.55, mode="paper", rationale="Mechanical ride entry",
            created_at=T0, strategy_bucket="earnings:ride",
        )
        store.resolve_thesis(thesis_id, outcome=net > 0, resolved_at=T0 + timedelta(days=when))
        store.record_decision(
            decision_id=f"dec-res-{thesis_id}", bot_id=MECH, decision_type="resolution",
            action="win" if net > 0 else "loss",
            inputs={"event_id": event_id, "symbol": sym, "net_return": net, "strategy_bucket": "earnings:ride"},
            mode="paper", as_of=T0 + timedelta(days=when), thesis_id=thesis_id,
        )

    # --- quality-gate veto of e-2 on the gated lane ---
    store.record_decision(
        decision_id="dec-skip-e2",
        bot_id=GATED,
        decision_type="entry",
        action="skipped_quality_gate",
        inputs={
            "event_id": "e-2",
            "symbol": "VETO",
            "strategy_bucket": "earnings:ride",
            "surprise_pct": 0.11,
            "quality": {
                "scored": True,
                "quality_score": 0.22,
                "stated_probability": 0.35,
                "flags": ["ensemble_unavailable"],
                "triage": {"worth_scoring": True, "reason": "one-off tax benefit"},
            },
        },
        mode="paper",
        as_of=T0 + timedelta(days=2),
        prompt_version="qscore-v1",
    )

    # --- open position on the gated lane (thesis t-open) ---
    store.record_thesis(
        thesis_id="t-open", bot_id=GATED, symbol="ABC", direction="long",
        stated_probability=0.62, mode="paper",
        rationale="Mechanical ride entry | quality: strong organic growth",
        created_at=T0 + timedelta(days=5), strategy_bucket="earnings:ride",
    )
    store.record_fill(
        fill_id="f-entry-open", order_id="o-entry-open", symbol="ABC", side="buy",
        quantity=100, price=10.0, slippage_pct=0.003, bot_id=GATED, mode="paper",
        filled_at=T0 + timedelta(days=6),
    )

    # snapshots: gated holds ABC marked at 11.0; mech is flat
    snap_ts = T0 + timedelta(days=12)
    store.record_snapshot(
        PortfolioSnapshot(
            cash=99_000.0,
            positions=[
                Position(symbol="ABC", quantity=100, avg_cost=10.0,
                         market_value=1_100.0, unrealized_pnl=100.0)
            ],
            total_equity=100_100.0,
            as_of=snap_ts,
        ),
        bot_id=GATED,
        mode="paper",
    )
    for i, equity in enumerate((100_000.0, 100_050.0, 100_100.0)):
        store.record_snapshot(
            PortfolioSnapshot(cash=equity, positions=[], total_equity=equity,
                              as_of=T0 + timedelta(days=4 * i)),
            bot_id=MECH,
            mode="paper",
        )
    # gated needs >=2 snapshots for the race chart
    store.record_snapshot(
        PortfolioSnapshot(cash=100_000.0, positions=[], total_equity=100_000.0, as_of=T0),
        bot_id=GATED,
        mode="paper",
    )

    # calibration rows across probability bins
    for i in range(12):
        thesis_id = f"t-cal-{i}"
        prob = 0.72 if i % 2 == 0 else 0.55
        outcome = i % 3 != 0  # 8/12 wins
        store.record_thesis(
            thesis_id=thesis_id, bot_id=GATED, symbol="CAL", direction="long",
            stated_probability=prob, mode="paper", strategy_bucket="earnings:ride",
        )
        store.record_calibration(
            thesis_id=thesis_id, bot_id=GATED, trade_type="earnings_ride",
            stated_probability=prob, outcome=outcome, bucket="medium_confidence",
            strategy_bucket="earnings:ride", resolved_at=T0 + timedelta(days=i),
        )

    # flow lens consensus (panel 6 reads this from the ops DB)
    store.record_flow_lens_consensus(
        logged_date="2026-06-11", symbol="NVDA", consensus_score=0.9,
        novelty_flag=False, model_count=4, prompt_count=8, models=["chatgpt", "claude"],
    )
    store.record_flow_lens_consensus(
        logged_date="2026-06-11", symbol="IONQ", consensus_score=0.55,
        novelty_flag=True, model_count=2, prompt_count=3, models=["chatgpt", "grok"],
    )
    return store


def _write_sidecar(path: Path) -> None:
    sidecar = {
        GATED: {
            "strategy": {
                "open": [
                    {
                        "symbol": "ABC",
                        "thesis_id": "t-open",
                        "entry_order_id": "o-entry-open",
                        "entry_ts": (T0 + timedelta(days=6)).isoformat(),
                        "event_id": "e-3",
                        "missing_position_ticks": 0,
                        "stated_probability": 0.62,
                        "entry_price": 10.0,
                        "high_water": 11.5,
                        "profit_taken": False,
                        "exit_order_ids": [],
                    }
                ],
                "closing": [],
            },
            "pending_orders": [],
        },
        MECH: {"strategy": {"open": [], "closing": []}, "pending_orders": []},
    }
    path.write_text(json.dumps(sidecar), encoding="utf-8")


def _write_logs(logs_dir: Path, day: date) -> None:
    """Reproduce the real mixed encoding: UTF-8 sentinel lines from PowerShell,
    UTF-16LE python output appended by `*>>`."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    iso = day.isoformat()

    summary = {
        "ran_at": f"{iso}T22:00:05+00:00",
        "universe_size": 740,
        "lanes": {GATED: {"fills": 1}, MECH: {"fills": 1}},
        "llm_spend": {
            "label": "paper-2c",
            "cap_usd": 5.0,
            "spent_usd": 0.8123,
            "calls": 9,
            "by_model": {"claude-opus-4-8": 0.61, "claude-haiku-4-5-20251001": 0.20},
        },
    }
    paper = logs_dir / f"paper-tick-{iso}.log"
    with paper.open("wb") as f:
        f.write(f"=== paper tick started {iso}T17:00:01.0000000-05:00 ===\r\n".encode("utf-8"))
        body = (
            f"2026-06-11 17:00:09 INFO paper tick complete: {json.dumps(summary)}\r\n"
        )
        f.write(body.encode("utf-16-le"))
        f.write("paper tick exit code: 0\r\n".encode("utf-8"))
        f.write(f"=== paper tick finished {iso}T17:00:10.0000000-05:00 ===\r\n".encode("utf-8"))

    flow = logs_dir / f"flow-lens-{iso}.log"
    with flow.open("wb") as f:
        f.write(f"=== flow lens run started {iso}T09:00:00.0000000-05:00 ===\r\n".encode("utf-8"))
        f.write("Est. API spend:  $0.0850\r\n".encode("utf-16-le"))
        f.write("flow lens exit code: 0\r\n".encode("utf-8"))
        f.write(f"=== flow lens run finished {iso}T09:01:00.0000000-05:00 ===\r\n".encode("utf-8"))

    crumbs = logs_dir / f"crumbs-{iso}.log"
    with crumbs.open("wb") as f:
        f.write(f"=== crumbs scan started {iso}T08:30:00.0000000-05:00 ===\r\n".encode("utf-8"))
        f.write("crumbs scanner exit code: 1\r\n".encode("utf-8"))
        f.write(f"=== crumbs scan finished {iso}T08:31:00.0000000-05:00 ===\r\n".encode("utf-8"))
    # No ingestion log: that job is 'pending' today.


def _write_crumbs_json(path: Path) -> None:
    alerts = [
        {
            "accession_number": "0001234567-26-001234",
            "form": "SC TO-I",
            "company": "Acme Holdings, Inc.",
            "cik": "1234567",
            "filed_at": "2026-06-10",
            "matched_phrase": "odd lot",
            "filing_url": "https://www.sec.gov/cgi-bin/browse-edgar?x=1",
        },
        {
            "accession_number": "0007654321-26-000987",
            "form": "SC TO-T",
            "company": "Beta Corp",
            "cik": "7654321",
            "filed_at": "2026-06-11",
            "matched_phrase": "fewer than 100 shares",
            "filing_url": "https://www.sec.gov/cgi-bin/browse-edgar?x=2",
        },
    ]
    path.write_text(json.dumps(alerts), encoding="utf-8")


# ---------------------------------------------------------------------------
# panel 1 — ops health
# ---------------------------------------------------------------------------


class TestOpsHealth:
    def test_job_statuses_from_mixed_encoding_logs(self, tmp_path) -> None:
        day = date(2026, 6, 11)
        _write_logs(tmp_path / "logs", day)
        statuses = {j.log_prefix: j for j in o.job_statuses(tmp_path / "logs", today=day)}

        assert statuses["paper-tick"].state == "ok"
        assert statuses["paper-tick"].exit_code == 0
        assert statuses["paper-tick"].finished_at is not None
        assert statuses["flow-lens"].state == "ok"
        assert statuses["crumbs"].state == "failed"
        assert statuses["crumbs"].exit_code == 1
        assert statuses["ingestion"].state == "pending"
        assert statuses["ingestion"].ran_today is False

    def test_daily_spend_parsed_from_logs(self, tmp_path) -> None:
        day = date(2026, 6, 11)
        _write_logs(tmp_path / "logs", day)
        spend = o.daily_spend(tmp_path / "logs", today=day)

        assert spend.paper_llm_usd == 0.8123
        assert spend.paper_llm_cap_usd == 5.0
        assert spend.flow_lens_usd == 0.085
        assert abs(spend.total_usd - 0.8973) < 1e-9
        assert spend.by_model["claude-opus-4-8"] == 0.61


# ---------------------------------------------------------------------------
# panel 2 — open positions + exit-rule state
# ---------------------------------------------------------------------------


class TestOpenPositions:
    def test_exit_rule_distances_from_snapshot_and_sidecar(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        sidecar_path = tmp_path / "sidecar.json"
        _write_sidecar(sidecar_path)
        sidecar = o.load_sidecar(sidecar_path)

        lane = d.open_positions(db, GATED, "paper", o.open_trades_for(sidecar, GATED))
        assert lane["total_equity"] == 100_100.0
        (pos,) = lane["positions"]

        # mark = 1100/100 = 11.0; entry 10.0; high-water 11.5 (from the sidecar)
        assert pos["mark"] == 11.0
        assert abs(pos["return_from_entry"] - 0.10) < 1e-9
        # hard stop at 10*(1-0.08)=9.20 -> (11-9.2)/11
        assert abs(pos["hard_stop_price"] - 9.2) < 1e-9
        assert abs(pos["to_hard_stop_pct"] - (11.0 - 9.2) / 11.0) < 1e-9
        # trail at 11.5*(1-0.07)=10.695 -> (11-10.695)/11
        assert abs(pos["trail_stop_price"] - 10.695) < 1e-9
        assert abs(pos["to_trail_stop_pct"] - (11.0 - 10.695) / 11.0) < 1e-9
        # profit-take not yet taken: +15% trigger - +10% so far = 5pp away
        assert abs(pos["to_profit_take_pct"] - 0.05) < 1e-9
        # time stop: entry T0+6d, snapshot T0+12d -> 8 days remain of 14
        assert abs(pos["days_to_time_stop"] - 8.0) < 1e-6
        assert pos["thesis_id"] == "t-open"

    def test_flat_lane_and_missing_sidecar_are_graceful(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        lane = d.open_positions(db, MECH, "paper", o.open_trades_for({}, MECH))
        assert lane["positions"] == []
        assert o.load_sidecar(tmp_path / "missing.json") == {}


# ---------------------------------------------------------------------------
# panel 3 — thesis browser
# ---------------------------------------------------------------------------


class TestThesisBrowser:
    def test_list_theses_includes_quality_and_flags(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        theses = {t["thesis_id"]: t for t in d.list_theses(db, mode="paper")}

        closed = theses["t-closed"]
        assert closed["quality_score"] == 0.81
        assert closed["flags"] == ["ensemble_disagreement"]
        assert closed["outcome"] == 1
        assert closed["surprise_pct"] == 0.20
        assert theses["t-open"]["outcome"] is None

    def test_gate_skips_surface_the_veto_reason(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        (skip,) = d.gate_skips(db, mode="paper")
        assert skip["symbol"] == "VETO"
        assert skip["quality_score"] == 0.22
        assert skip["flags"] == ["ensemble_unavailable"]
        assert skip["triage_reason"] == "one-off tax benefit"

    def test_thesis_detail_collects_decisions_and_fills(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        detail = d.thesis_detail(db, "t-closed")
        assert detail is not None
        assert [x["decision_type"] for x in detail["decisions"]] == ["entry", "resolution"]
        assert detail["quality"]["quality_score"] == 0.81
        assert [f["fill_id"] for f in detail["fills"]] == ["f-entry-closed", "f-exit-closed"]

    def test_drilldown_from_fill_finds_the_thesis(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        assert d.thesis_id_for_fill(db, "f-exit-closed") == "t-closed"
        assert d.thesis_id_for_fill(db, "f-entry-open") == "t-open"
        fills = d.recent_fills(db, mode="paper")
        by_id = {f["fill_id"]: f for f in fills}
        assert by_id["f-entry-closed"]["thesis_id"] == "t-closed"


# ---------------------------------------------------------------------------
# panel 4 — race view
# ---------------------------------------------------------------------------


class TestRaceView:
    def test_per_event_join_and_cumulative(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        race = d.race(db, "earnings_ride", mode="paper")

        events = {e["event_id"]: e for e in race["events"]}
        e1, e2 = events["e-1"], events["e-2"]
        assert e1["gated"] == 0.10 and e1["mech"] == 0.08
        assert e2.get("gated") is None and e2["gate_skipped"] is True
        assert e2["mech"] == -0.04
        # cumulative after both events
        last = race["events"][-1]
        assert abs(last["cum_gated"] - 0.10) < 1e-9
        assert abs(last["cum_mech"] - 0.04) < 1e-9

        gated_summary = race["summary"]["gated"]
        mech_summary = race["summary"]["mech"]
        assert gated_summary["resolved_trades"] == 1
        assert gated_summary["hit_rate"] == 1.0
        assert mech_summary["resolved_trades"] == 2
        assert mech_summary["hit_rate"] == 0.5

    def test_curves_present_for_both_lanes(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        race = d.race(db, "earnings_ride", mode="paper")
        assert len(race["curves"][GATED]) >= 2
        assert len(race["curves"][MECH]) >= 2


# ---------------------------------------------------------------------------
# panel 5 — calibration curves
# ---------------------------------------------------------------------------


class TestCalibrationCurves:
    def test_bins_report_stated_vs_realized(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        curves = d.calibration_curves(db)
        ride = next(
            c for c in curves if c["bot_id"] == GATED and c["strategy_bucket"] == "earnings:ride"
        )
        assert ride["samples"] == 12
        # probs were 0.55 and 0.72 -> bins [0.5,0.6) and [0.7,0.8)
        lows = {b["bin_low"] for b in ride["bins"]}
        assert {0.5, 0.7} <= lows
        for b in ride["bins"]:
            assert 0.0 <= b["hit_rate"] <= 1.0
            assert b["count"] > 0


# ---------------------------------------------------------------------------
# panel 6 — crumbs + flow lens
# ---------------------------------------------------------------------------


class TestSignalsPanel:
    def test_crumbs_feed_newest_first(self, tmp_path) -> None:
        feed = tmp_path / "crumbs_alerts.json"
        _write_crumbs_json(feed)
        alerts = o.crumbs_alerts(feed)
        assert [a["company"] for a in alerts] == ["Beta Corp", "Acme Holdings, Inc."]
        assert o.crumbs_alerts(tmp_path / "missing.json") == []

    def test_flow_lens_board_latest_day(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        board = d.flow_lens_board(db)
        assert board["logged_date"] == "2026-06-11"
        assert board["rows"][0]["symbol"] == "NVDA"
        novel = next(r for r in board["rows"] if r["symbol"] == "IONQ")
        assert novel["novelty_flag"] is True


# ---------------------------------------------------------------------------
# panel 7 / rendering — all panels render read-only, missing artifacts ok
# ---------------------------------------------------------------------------


class TestRendering:
    def _paths(self, tmp_path) -> dict:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        _write_sidecar(tmp_path / "sidecar.json")
        _write_logs(tmp_path / "logs", date.today())
        _write_crumbs_json(tmp_path / "crumbs_alerts.json")
        return {
            "db_path": db,
            "ops_db_path": db,
            "logs_dir": tmp_path / "logs",
            "sidecar_path": tmp_path / "sidecar.json",
            "crumbs_json": tmp_path / "crumbs_alerts.json",
        }

    def test_main_page_renders_scoreboard_and_ops_strip(self, tmp_path) -> None:
        p = self._paths(tmp_path)
        page = render_page(
            p["db_path"],
            assumed_slippage_pct=0.003,
            ops_db_path=p["ops_db_path"],
            logs_dir=p["logs_dir"],
            sidecar_path=p["sidecar_path"],
            crumbs_json=p["crumbs_json"],
            refresh_seconds=60,
        )
        assert "strategy scoreboard" in page.lower()
        assert "ops-strip" in page
        assert "Scheduled jobs today" in page
        assert "/strategy/earnings_ride" in page
        assert "http-equiv='refresh' content='60'" in page
        assert "nav-header" in page

        strat = render_strategy_page(
            p["db_path"],
            "earnings_ride",
            assumed_slippage_pct=0.003,
            ops_db_path=p["ops_db_path"],
            logs_dir=p["logs_dir"],
            sidecar_path=p["sidecar_path"],
            crumbs_json=p["crumbs_json"],
        )
        assert "Open positions" in strat
        assert "Thesis browser" in strat
        assert "Brain-vs-coin race" in strat
        assert "Calibration" in strat
        assert "/thesis/t-open" in strat
        assert "ensemble_disagreement" in strat
        assert "gate veto" in strat
        assert "earnings:ride" in strat
        assert "<svg" in strat

    def test_main_page_renders_with_no_paper_db_or_artifacts(self, tmp_path) -> None:
        page = render_page(
            tmp_path / "nope.db",
            assumed_slippage_pct=0.003,
            ops_db_path=tmp_path / "nope.db",
            logs_dir=tmp_path / "empty-logs",
            sidecar_path=tmp_path / "nope.json",
            crumbs_json=tmp_path / "nope2.json",
        )
        assert "Scheduled jobs today" in page
        assert "No paper DB yet" in page

    def test_thesis_page_shows_full_audit_trail(self, tmp_path) -> None:
        p = self._paths(tmp_path)
        page = render_thesis_page(p["db_path"], "t-closed")
        assert "WIN" in page
        assert "qscore-v1" in page
        assert "ensemble_disagreement" in page
        assert "f-entry-closed" not in page  # raw ids not shown; fills table is
        assert "Decisions" in page and "Fills" in page

    def test_thesis_page_handles_unknown_id(self, tmp_path) -> None:
        p = self._paths(tmp_path)
        page = render_thesis_page(p["db_path"], "does-not-exist")
        assert "Thesis not found" in page

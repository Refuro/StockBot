"""Dashboard v3 Phase 2 — presentation / IA tests."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stockbot.core.state.store import StateStore
from stockbot.types import PortfolioSnapshot
from stockbot.dashboard.app import (
    render_execution_page,
    render_lens_page,
    render_ops_page,
    render_page,
    render_strategy_page,
)

UTC = timezone.utc
T0 = datetime(2026, 6, 1, 21, 0, tzinfo=UTC)


def _write_logs(logs_dir: Path, day: date) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    for prefix in ("ingestion", "crumbs", "flow-lens", "paper-tick"):
        (logs_dir / f"{prefix}-{day.isoformat()}.log").write_text(
            f"=== {prefix} started {day.isoformat()}T08:00:00 ===\n"
            f"=== {prefix} finished {day.isoformat()}T08:05:00 ===\n"
            "exit code: 0\n",
            encoding="utf-8",
        )


def _seed_minimal_db(db_path: Path) -> None:
    store = StateStore(db_path)
    store.start_run(
        run_id="run-v3",
        bot_id="proportionality",
        mode="paper",
        config_hash="cafe",
        model_versions={},
        log_path="logs/run.log",
        started_at=T0,
    )
    store.finish_run("run-v3", status="completed", finished_at=T0 + timedelta(minutes=5))
    store.record_thesis(
        thesis_id="t-ride",
        bot_id="proportionality",
        symbol="WIN",
        direction="long",
        stated_probability=0.72,
        mode="paper",
        strategy_bucket="earnings:ride",
        created_at=T0,
    )
    store.record_decision(
        decision_id="res-g",
        bot_id="proportionality",
        decision_type="resolution",
        action="win",
        inputs={"event_id": "e1", "symbol": "WIN", "net_return": 0.1, "strategy_bucket": "earnings:ride"},
        mode="paper",
        as_of=T0 + timedelta(days=5),
        thesis_id="t-ride",
    )


class TestScoreboardHome:
    def test_empty_db(self, tmp_path: Path) -> None:
        page = render_page(
            tmp_path / "empty.db",
            assumed_slippage_pct=0.003,
            logs_dir=tmp_path / "logs",
            crumbs_json=tmp_path / "crumbs.json",
        )
        assert "strategy scoreboard" in page.lower()
        assert "ops-strip" in page
        assert "Scheduled jobs today" in page
        assert "sb-cards" not in page or "No paper DB yet" in page

    def test_populated_scoreboard(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_minimal_db(db)
        _write_logs(tmp_path / "logs", date.today())
        page = render_page(
            db,
            assumed_slippage_pct=0.003,
            logs_dir=tmp_path / "logs",
            crumbs_json=tmp_path / "crumbs.json",
        )
        assert "sb-card" in page
        assert "/strategy/earnings_ride" in page
        assert "Earnings ride" in page
        assert "Proportionality" in page


class TestStrategyPage:
    def test_unknown_strategy(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_strategy_page(db, "not_a_strategy", assumed_slippage_pct=0.003)
        assert "Unknown strategy" in page
        assert "not_a_strategy" in page

    def test_earnings_ride_populated(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_minimal_db(db)
        page = render_strategy_page(db, "earnings_ride", assumed_slippage_pct=0.003)
        assert "Brain-vs-coin race" in page
        assert "Calibration" in page
        assert "Thesis browser" in page
        assert "WIN" in page


class TestGlobalPages:
    def test_execution_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_execution_page(db, assumed_slippage_pct=0.003)
        assert "Execution" in page
        assert "Closed trades" in page
        assert "Slippage" in page

    def test_ops_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_ops_page(db, assumed_slippage_pct=0.003, logs_dir=tmp_path / "logs")
        assert "Ops health" in page or "Ops" in page
        assert "Shadow rehearsal" in page
        assert "Go-live reference" in page

    def test_lens_crumbs_ran_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        crumbs = tmp_path / "crumbs.json"
        crumbs.write_text("[]", encoding="utf-8")
        page = render_lens_page(
            db,
            assumed_slippage_pct=0.003,
            crumbs_json=crumbs,
        )
        assert "none found" in page.lower() or "Last scan" in page

    def test_lens_crumbs_absent(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_lens_page(
            db,
            assumed_slippage_pct=0.003,
            crumbs_json=tmp_path / "missing.json",
        )
        assert "Didn't run yet" in page or "didn't run" in page.lower()

    def test_lens_explainer_copy(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_lens_page(
            db,
            assumed_slippage_pct=0.003,
            crumbs_json=tmp_path / "missing.json",
        )
        assert "What am I looking at?" in page
        assert "odd-lot tender" in page.lower()
        assert "F2 study" in page

    def test_crash_fade_fade_decisions_explainer(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        _seed_minimal_db(db)
        page = render_strategy_page(db, "crash_fade", assumed_slippage_pct=0.003)
        assert "Fade decisions" in page
        assert "What am I looking at?" in page
        assert "skipped_fade_gate" in page
        assert "ensemble p" in page


class TestPaperTickOpsReconciliation:
    def _write_failed_paper_tick_log(self, logs_dir: Path, day: date) -> None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / f"paper-tick-{day.isoformat()}.log").write_text(
            f"=== paper tick started {day.isoformat()}T17:00:00 ===\n"
            f"=== paper tick finished {day.isoformat()}T17:05:00 ===\n"
            "exit code: -1\n",
            encoding="utf-8",
        )

    def _seed_completed_run_today(self, db_path: Path, day: date) -> None:
        store = StateStore(db_path)
        finished = datetime.combine(day, datetime.min.time()).replace(
            hour=22, tzinfo=UTC
        )
        store.start_run(
            run_id="run-recovered",
            bot_id="proportionality",
            mode="paper",
            config_hash="cafe",
            model_versions={},
            log_path="logs/run.log",
            started_at=finished - timedelta(minutes=5),
        )
        store.finish_run("run-recovered", status="completed", finished_at=finished)
        # A genuinely recovered tick also writes snapshots; a bare "completed"
        # run row is what a CRASHED tick leaves behind (see the 2026-08 outage).
        store.record_snapshot(
            PortfolioSnapshot(cash=1.0, positions=[], total_equity=1.0, as_of=finished),
            bot_id="proportionality",
            mode="paper",
        )

    def test_ops_reconciles_failed_log_with_db_completed(self, tmp_path: Path) -> None:
        day = date(2026, 6, 18)
        db = tmp_path / "paper.db"
        StateStore(db)
        self._seed_completed_run_today(db, day)
        logs = tmp_path / "logs"
        self._write_failed_paper_tick_log(logs, day)
        page = render_ops_page(
            db,
            assumed_slippage_pct=0.003,
            logs_dir=logs,
            today=day,
        )
        assert "recovered by re-run" in page
        assert "today's tick FAILED" not in page
        assert "chip failed" not in page or "Paper tick exit -1" not in page

    def test_home_ops_strip_reconciles_failed_log(self, tmp_path: Path) -> None:
        day = date(2026, 6, 18)
        db = tmp_path / "paper.db"
        StateStore(db)
        self._seed_completed_run_today(db, day)
        logs = tmp_path / "logs"
        self._write_failed_paper_tick_log(logs, day)
        page = render_page(
            db,
            assumed_slippage_pct=0.003,
            logs_dir=logs,
            crumbs_json=tmp_path / "crumbs.json",
            today=day,
        )
        assert "recovered by re-run" in page
        assert "Today's paper tick" in page
        assert "<span class='no'>FAILED</span>" not in page

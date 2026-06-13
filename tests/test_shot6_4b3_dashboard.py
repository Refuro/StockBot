"""Ticket 4b-3 — dashboard readability layer (summary strip, explainers, verdicts).

Deterministic unit tests only; no writes from dashboard code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from stockbot.dashboard import verdicts as v
from stockbot.dashboard.app import (
    _summary_strip,
    render_glossary_page,
    render_page,
    render_strategy_page,
)
from stockbot.dashboard.glossary import FLAG_TOOLTIPS, GLOSSARY_TERMS
from tests.test_4b2_dashboard_v2 import GATED, MECH, _seed_paper_db, _write_crumbs_json, _write_logs, _write_sidecar

UTC = timezone.utc


class TestRaceVerdict:
    def test_n_zero_no_resolved_trades(self) -> None:
        race = {
            "summary": {
                "gated": {"resolved_trades": 0},
                "mech": {"resolved_trades": 0},
            },
            "curves": {GATED: [], MECH: []},
        }
        line = v.race_verdict(race, gated_bot_id=GATED, mech_bot_id=MECH)
        assert line == "No trades have resolved yet — nothing to compare. The race just started."

    def test_ahead_with_small_sample(self) -> None:
        race = {
            "summary": {
                "gated": {"resolved_trades": 1},
                "mech": {"resolved_trades": 2},
            },
            "curves": {
                GATED: [
                    {"total_equity": 100_000.0, "as_of": "2026-06-01"},
                    {"total_equity": 100_200.0, "as_of": "2026-06-12"},
                ],
                MECH: [
                    {"total_equity": 100_000.0, "as_of": "2026-06-01"},
                    {"total_equity": 100_100.0, "as_of": "2026-06-12"},
                ],
            },
        }
        line = v.race_verdict(race, gated_bot_id=GATED, mech_bot_id=MECH)
        assert "ahead of" in line
        assert "0.10%" in line or "0.20%" in line  # 0.2% vs 0.1% diff = 0.10pp
        assert "too early to mean anything until ~40 trades" in line

    def test_behind_meaningful_sample(self) -> None:
        race = {
            "summary": {
                "gated": {"resolved_trades": 45},
                "mech": {"resolved_trades": 50},
            },
            "curves": {
                GATED: [
                    {"total_equity": 100_000.0, "as_of": "2026-06-01"},
                    {"total_equity": 99_000.0, "as_of": "2026-06-12"},
                ],
                MECH: [
                    {"total_equity": 100_000.0, "as_of": "2026-06-01"},
                    {"total_equity": 101_000.0, "as_of": "2026-06-12"},
                ],
            },
        }
        line = v.race_verdict(race, gated_bot_id=GATED, mech_bot_id=MECH)
        assert "behind" in line
        assert "a meaningful sample now" in line

    def test_tied_lanes(self) -> None:
        race = {
            "summary": {"gated": {"resolved_trades": 5}, "mech": {"resolved_trades": 5}},
            "curves": {
                GATED: [
                    {"total_equity": 100_000.0, "as_of": "2026-06-01"},
                    {"total_equity": 100_050.0, "as_of": "2026-06-12"},
                ],
                MECH: [
                    {"total_equity": 100_000.0, "as_of": "2026-06-01"},
                    {"total_equity": 100_050.0, "as_of": "2026-06-12"},
                ],
            },
        }
        line = v.race_verdict(race, gated_bot_id=GATED, mech_bot_id=MECH)
        assert "tied with" in line

    def test_insufficient_snapshots(self) -> None:
        race = {
            "summary": {"gated": {"resolved_trades": 1}, "mech": {"resolved_trades": 1}},
            "curves": {GATED: [{"total_equity": 100_000.0, "as_of": "2026-06-01"}], MECH: []},
        }
        line = v.race_verdict(race, gated_bot_id=GATED, mech_bot_id=MECH)
        assert "Not enough equity snapshots" in line


class TestCalibrationVerdict:
    def test_empty_rows(self) -> None:
        lines = v.calibration_verdicts([])
        assert len(lines) == 1
        assert "No calibration data yet" in lines[0]

    def test_not_yet_eligible(self) -> None:
        row = {
            "strategy_bucket": "earnings:ride",
            "count": 3,
            "mean_brier": 0.18,
            "calibrated": False,
        }
        line = v.calibration_verdict_line(row)
        assert "3 of 10" in line
        assert "not yet eligible" in line

    def test_eligible_bucket(self) -> None:
        row = {
            "strategy_bucket": "earnings:fade",
            "count": 12,
            "mean_brier": 0.21,
            "calibrated": True,
        }
        line = v.calibration_verdict_line(row)
        assert "12 of 10" in line
        assert "eligible" in line
        assert "not yet eligible" not in line

    def test_zero_fade_sparse_june(self) -> None:
        """June may have zero fades — empty calibration is explicit, not an error."""
        lines = v.calibration_verdicts([])
        assert all(isinstance(x, str) and x for x in lines)


class TestGlossaryPage:
    def test_glossary_returns_key_terms(self) -> None:
        page = render_glossary_page()
        assert "Glossary" in page
        for term in ("lane", "Brier score", "calibration gate", "paper vs pilot vs live"):
            assert term in page
            assert term in GLOSSARY_TERMS

    def test_flag_tooltips_cover_common_flags(self) -> None:
        assert "ensemble_disagreement" in FLAG_TOOLTIPS
        assert "counter_pass_veto" in FLAG_TOOLTIPS
        assert "no_source_doc" in FLAG_TOOLTIPS


class TestSummaryStripAndExplainers:
    def test_summary_strip_empty_db_does_not_throw(self) -> None:
        from stockbot.dashboard.app import DashboardPaths

        paths = DashboardPaths(
            db_path="data/missing.db",
            ops_db_path="data/missing.db",
            logs_dir="logs",
        )
        html_out = _summary_strip(paths, today=date(2026, 6, 12), now=datetime(2026, 6, 12, 12, tzinfo=UTC))
        assert "What is this?" in html_out
        assert "Is real money at risk?" in html_out
        assert "fake money" in html_out
        assert "No lane data yet" in html_out

    def test_summary_strip_holiday_aware(self, tmp_path) -> None:
        from stockbot.dashboard.app import DashboardPaths

        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        paths = DashboardPaths(db_path=str(db), ops_db_path=str(db), logs_dir=str(tmp_path / "logs"))
        # 2026-06-19 is Juneteenth (NYSE closed)
        html_out = _summary_strip(paths, today=date(2026, 6, 19), now=datetime(2026, 6, 19, 12, tzinfo=UTC))
        assert "Market closed today" in html_out
        assert "Mon" in html_out  # next trading day after Fri Jun 19 holiday is Mon Jun 22

    def test_main_page_has_readability_layer(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed_paper_db(db)
        _write_sidecar(tmp_path / "sidecar.json")
        _write_logs(tmp_path / "logs", date(2026, 6, 11))
        _write_crumbs_json(tmp_path / "crumbs_alerts.json")
        page = render_page(
            db,
            assumed_slippage_pct=0.003,
            ops_db_path=db,
            logs_dir=tmp_path / "logs",
            sidecar_path=tmp_path / "sidecar.json",
            crumbs_json=tmp_path / "crumbs_alerts.json",
            today=date(2026, 6, 12),
            now=datetime(2026, 6, 12, 18, 0, tzinfo=UTC),
        )
        assert "ops-strip" in page
        assert "backtest" in page and "paper ▶" in page
        assert "glossary" in page and "/glossary" in page
        assert "nav-header" in page
        strat = render_strategy_page(
            db,
            "earnings_ride",
            assumed_slippage_pct=0.003,
            ops_db_path=db,
            logs_dir=tmp_path / "logs",
            sidecar_path=tmp_path / "sidecar.json",
            crumbs_json=tmp_path / "crumbs_alerts.json",
        )
        assert "What am I looking at?" in strat
        assert "class='verdict'" in strat
        assert "title=" in strat
        assert "ensemble_disagreement" in strat
        assert FLAG_TOOLTIPS["ensemble_disagreement"][:20] in strat

    def test_glossary_route_via_render(self) -> None:
        page = render_glossary_page()
        assert page.startswith("<!doctype html>")
        assert "kill switch" in page.lower() or "kill switch" in page

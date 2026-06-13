"""Shot 10 — dashboard clarity pass.

Covers the four owner-driven fixes, all offline and read-only:
1. Central-time render helper (incl. a DST boundary), stored data still UTC.
2. Always-visible flag legend, sourced from glossary.py (no drift).
3. Surprise column: actual-vs-consensus EPS + small-denominator marker.
4. AI-ensemble-health indicator derived from the flags already in the DB.

The existing 4b-2 / 4b-3 dashboard tests stay green; this file only adds.
"""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta, timezone

from stockbot.core.state.store import StateStore
from stockbot.dashboard import data as d
from stockbot.dashboard import glossary as g
from stockbot.dashboard import app
from stockbot.dashboard.app import (
    DISPLAY_TZ_NAME,
    _flag_legend,
    _fmt_central,
    _surprise_cell,
    render_page,
    render_strategy_page,
    render_thesis_page,
)

UTC = timezone.utc
GATED = "proportionality"
MECH = "proportionality_mech"


# ---------------------------------------------------------------------------
# seed: a small paper DB with EPS detail + ensemble flags
# ---------------------------------------------------------------------------


def _record_entry(
    store: StateStore,
    thesis_id: str,
    symbol: str,
    created: datetime,
    *,
    surprise: float,
    actual: float,
    consensus: float,
    flags: list[str],
) -> None:
    store.record_thesis(
        thesis_id=thesis_id,
        bot_id=GATED,
        symbol=symbol,
        direction="long",
        stated_probability=0.6,
        mode="paper",
        rationale="ride entry",
        created_at=created,
        strategy_bucket="earnings:ride",
    )
    store.record_decision(
        decision_id=f"dec-{thesis_id}",
        bot_id=GATED,
        decision_type="entry",
        action="buy",
        inputs={
            "event_id": f"e-{thesis_id}",
            "symbol": symbol,
            "surprise_pct": surprise,
            "actual_eps": actual,
            "consensus_eps": consensus,
            "quality": {
                "scored": True,
                "quality_score": 0.7,
                "stated_probability": 0.6,
                "flags": flags,
                "triage": {"worth_scoring": True, "reason": "real results"},
            },
            "stated_probability": 0.6,
        },
        mode="paper",
        as_of=created,
        thesis_id=thesis_id,
        model_id="claude-opus-4-8",
        prompt_version="qscore-v1",
    )


def _seed(db_path, *, latest_flagged: bool) -> StateStore:
    store = StateStore(db_path)
    base = datetime(2026, 6, 10, 21, 0, tzinfo=UTC)
    # older: clean ensemble, normal-sized beat
    _record_entry(store, "t-older", "AAA", base, surprise=0.10,
                  actual=1.10, consensus=1.00, flags=[])
    # newest: tiny consensus (small-denominator) + optionally flagged ensemble
    _record_entry(
        store, "t-newest", "BBB", base + timedelta(days=1),
        surprise=1.0, actual=0.08, consensus=0.04,
        flags=["ensemble_unavailable"] if latest_flagged else [],
    )
    return store


# ---------------------------------------------------------------------------
# 1 — Central time helper
# ---------------------------------------------------------------------------


class TestCentralTimeHelper:
    def test_summer_renders_cdt_with_label(self) -> None:
        # 2026-06-12T03:00 UTC -> 2026-06-11 10:00 PM CDT (the handoff example)
        assert _fmt_central("2026-06-12T03:00:00+00:00") == "2026-06-11 10:00 PM CDT"

    def test_winter_renders_cst(self) -> None:
        out = _fmt_central("2026-01-12T03:00:00+00:00")
        assert out == "2026-01-11 9:00 PM CST"

    def test_dst_boundary_spring_forward(self) -> None:
        # US DST begins 2026-03-08 02:00 local (CST -> CDT).
        before = _fmt_central("2026-03-08T07:00:00+00:00")  # 1:00 AM CST
        after = _fmt_central("2026-03-08T08:00:00+00:00")   # 3:00 AM CDT
        assert before.endswith("CST") and "1:00 AM" in before
        assert after.endswith("CDT") and "3:00 AM" in after

    def test_naive_timestamp_assumed_utc(self) -> None:
        assert _fmt_central("2026-06-12T03:00:00") == "2026-06-11 10:00 PM CDT"

    def test_microseconds_and_none(self) -> None:
        assert _fmt_central("2026-06-12T03:00:00.123456+00:00").endswith("CDT")
        assert _fmt_central(None) == "—"
        assert _fmt_central("not-a-date") == "—"

    def test_timezone_is_one_changeable_constant(self) -> None:
        assert DISPLAY_TZ_NAME == "America/Chicago"
        assert app.DISPLAY_TZ.key == "America/Chicago"

    def test_page_labels_times_and_keeps_db_utc(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=False)
        page = render_page(db, assumed_slippage_pct=0.003, ops_db_path=db,
                           logs_dir=tmp_path / "logs", today=date(2026, 6, 12),
                           now=datetime(2026, 6, 12, 18, tzinfo=UTC))
        assert "US Central" in page and "CDT/CST" in page
        assert "Stored data is UTC" in page
        # a Central label actually appears in a rendered timestamp cell
        assert "CDT" in page or "CST" in page


# ---------------------------------------------------------------------------
# 2 — flag legend (always visible, sourced from glossary)
# ---------------------------------------------------------------------------


class TestFlagLegend:
    def test_covers_required_flags_when_db_empty(self) -> None:
        legend = _flag_legend(set())
        assert "What the flags mean" in legend
        for flag in g.LEGEND_FLAGS:
            assert flag in legend
            # text is the single source of truth from glossary.py (no drift)
            assert html.escape(g.FLAG_TOOLTIPS[flag]) in legend

    def test_includes_db_flags_and_normalizes_variants(self) -> None:
        legend = _flag_legend({"forced_flow:index_exclusion", "strong_model_unparseable"})
        assert "strong_model_unparseable" in legend
        assert html.escape(g.FLAG_TOOLTIPS["strong_model_unparseable"]) in legend
        # the prefix:detail variant collapses to its base term
        assert "<span class='flag'>forced_flow</span>" in legend
        assert "index_exclusion" not in legend

    def test_flag_text_tolerates_variants(self) -> None:
        assert g.flag_text("forced_flow:fund_liquidation") == g.FLAG_TOOLTIPS["forced_flow"]
        assert g.flag_text("ensemble_unavailable") == g.FLAG_TOOLTIPS["ensemble_unavailable"]
        assert g.flag_text("totally_unknown_flag") == "totally_unknown_flag"

    def test_legend_renders_on_page_with_db_flags_explained(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=True)
        page = render_strategy_page(db, "earnings_ride", assumed_slippage_pct=0.003,
                                      ops_db_path=db, logs_dir=tmp_path / "logs")
        assert "flag-legend" in page
        assert "ensemble_unavailable" in page
        assert html.escape(g.FLAG_TOOLTIPS["ensemble_unavailable"]) in page

    def test_required_flags_all_have_glossary_text(self) -> None:
        for flag in g.LEGEND_FLAGS:
            assert flag in g.FLAG_TOOLTIPS
            assert g.FLAG_TOOLTIPS[flag]


# ---------------------------------------------------------------------------
# 3 — surprise column: actual vs consensus + small-denominator marker
# ---------------------------------------------------------------------------


class TestSurpriseCell:
    def test_shows_actual_vs_consensus(self) -> None:
        cell = _surprise_cell(0.10, actual_eps=1.10, consensus_eps=1.00)
        assert "+10.0%" in cell
        assert "act $1.10 vs est $1.00" in cell
        assert "tiny est." not in cell

    def test_small_denominator_marked(self) -> None:
        cell = _surprise_cell(1.0, actual_eps=0.08, consensus_eps=0.04)
        assert "+100.0%" in cell
        assert "tiny est." in cell
        assert "act $0.08 vs est $0.04" in cell

    def test_missing_surprise(self) -> None:
        assert _surprise_cell(None) == "<td>—</td>"

    def test_negative_without_eps_has_no_detail(self) -> None:
        cell = _surprise_cell(-0.05)
        assert "-5.0%" in cell
        assert "act $" not in cell
        assert "class='no'" in cell

    def test_thesis_browser_surfaces_eps_and_marker(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=False)
        theses = {t["thesis_id"]: t for t in d.list_theses(db, mode="paper")}
        assert theses["t-older"]["actual_eps"] == 1.10
        assert theses["t-older"]["consensus_eps"] == 1.00
        page = render_strategy_page(db, "earnings_ride", assumed_slippage_pct=0.003,
                                      ops_db_path=db, logs_dir=tmp_path / "logs")
        assert "act $1.10 vs est $1.00" in page  # normal beat
        assert "tiny est." in page               # 0.08 vs 0.04 small denominator
        assert "actual EPS" in page and "consensus EPS" in page


# ---------------------------------------------------------------------------
# 4 — AI ensemble-health indicator
# ---------------------------------------------------------------------------


class TestEnsembleHealth:
    def test_degraded_when_latest_run_flagged(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=True)
        health = d.ensemble_health(db, bot_id=GATED, mode="paper")
        assert health["degraded"] is True
        assert health["flagged"] == 1
        assert health["checked"] == 1  # only the newest run's theses are graded

    def test_ok_when_latest_run_clean(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=False)
        health = d.ensemble_health(db, bot_id=GATED, mode="paper")
        assert health["degraded"] is False
        assert health["flagged"] == 0

    def test_empty_db_is_not_degraded(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)  # schema only, no theses
        health = d.ensemble_health(db, bot_id=GATED, mode="paper")
        assert health == {"checked": 0, "flagged": 0, "degraded": False, "as_of": None}

    def test_warning_renders_on_page_when_degraded(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=True)
        page = render_page(db, assumed_slippage_pct=0.003, ops_db_path=db,
                           logs_dir=tmp_path / "logs")
        assert "unavailable on the last gated run" in page
        assert "Claude-only" in page

    def test_no_warning_when_clean(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=False)
        page = render_page(db, assumed_slippage_pct=0.003, ops_db_path=db,
                           logs_dir=tmp_path / "logs")
        # the warning banner is absent (its CSS class + the legend term still exist)
        assert "unavailable on the last gated run" not in page
        assert "GPT cross-check OK" in page


# ---------------------------------------------------------------------------
# average-joe: can the owner still answer the three questions, now unconfused?
# ---------------------------------------------------------------------------


class TestAverageJoeUnconfused:
    def test_page_answers_questions_without_flag_or_surprise_confusion(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=True)
        page = render_page(db, assumed_slippage_pct=0.003, ops_db_path=db,
                           logs_dir=tmp_path / "logs", today=date(2026, 6, 12),
                           now=datetime(2026, 6, 12, 18, tzinfo=UTC))
        assert "Paper tick" in page
        assert "sb-card" in page or "/strategy/" in page
        strat = render_strategy_page(db, "earnings_ride", assumed_slippage_pct=0.003,
                                       ops_db_path=db, logs_dir=tmp_path / "logs")
        assert "What the flags mean" in strat
        assert "act $1.10 vs est $1.00" in strat
        assert "tiny est." in strat

    def test_empty_db_page_still_renders_clean(self, tmp_path) -> None:
        page = render_page(tmp_path / "nope.db", assumed_slippage_pct=0.003,
                           ops_db_path=tmp_path / "nope.db",
                           logs_dir=tmp_path / "empty-logs")
        assert "US Central" in page
        assert "Scheduled jobs today" in page

    def test_thesis_detail_times_are_central(self, tmp_path) -> None:
        db = tmp_path / "paper.db"
        _seed(db, latest_flagged=False)
        page = render_thesis_page(db, "t-older")
        assert "CDT" in page or "CST" in page

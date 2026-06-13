"""Shot 13 — AI debate view, gate scoreboard, and Tier-2 AI-value trackers.

Tests:
1. Debate reconstruction — entered and vetoed fade cases (fields surfaced correctly).
2. Debate view handles ride theses (quality section, not debate).
3. Degraded / old rows with missing fields don't crash.
4. ensemble_unavailable degrades gracefully.
5. Gate scoreboard renders with both empty and populated ledgers.
6. Tier-2 model_disagreement_stats and veto_value_tally with zero data.
7. Tier-2 panels handle empty DB without crashing.
8. Debates index page renders.
9. render_thesis_page renders debate for all edge cases without crashing.
10. Existing dashboard panels are not broken (regression guard).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stockbot.core.state.store import StateStore
from stockbot.dashboard import data as d
from stockbot.dashboard.app import (
    _debate_section,
    render_debates_page,
    render_page,
    render_strategy_page,
    render_thesis_page,
)

UTC = timezone.utc
BOT_FADE = "proportionality"
BOT_MECH = "proportionality_mech"

# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


def _make_counter_pass_entered() -> dict:
    """Realistic counter_pass for an entered fade (final_prob >= 0.55)."""
    return {
        "argument": (
            "The company's guidance cut represents a permanent re-rating, not an "
            "overreaction. Margins compress structurally as input costs remain "
            "elevated. The market-cap loss is entirely justified."
        ),
        "strongest_point": "Structural margin compression is not a one-quarter event.",
        "counter_model_id": "gpt-5.5",
        "reread_model_id": "claude-opus-4-8",
        "revised_probability": 0.60,
        "final_probability": 0.58,
        "reread_rationale": "Counter-argument raises valid cost concerns but the "
        "selloff already prices in two years of compression.",
        "vetoed": False,
    }


def _make_counter_pass_vetoed() -> dict:
    """Realistic counter_pass for a vetoed fade (final_prob < 0.55)."""
    return {
        "argument": (
            "Revenue recognition is in question: the company pre-announced a "
            "material weakness in internal controls for the prior three quarters. "
            "Restatements could reduce earnings by 40 percent."
        ),
        "strongest_point": "A restatement risk is not captured in the ensemble probability.",
        "counter_model_id": "gpt-5.5",
        "reread_model_id": "claude-opus-4-8",
        "revised_probability": 0.48,
        "final_probability": 0.47,
        "reread_rationale": "The restatement risk is real and material; I lower my probability.",
        "vetoed": True,
    }


def _fade_gate_inputs(
    *,
    entered: bool,
    ensemble_prob: float = 0.63,
    flags: list[str] | None = None,
    counter_pass: dict | None = None,
) -> dict:
    """Build a fade_gate inputs dict that mirrors what ride.py stores."""
    if flags is None:
        flags = []
    cp = counter_pass or (_make_counter_pass_entered() if entered else _make_counter_pass_vetoed())
    if entered and "counter_pass_veto" not in flags:
        pass
    elif not entered and "counter_pass_veto" not in flags and cp.get("vetoed"):
        flags = list(flags) + ["counter_pass_veto"]
    return {
        "scored": True,
        "classification": "overreaction",
        "stated_probability": cp.get("final_probability", ensemble_prob),
        "ensemble_probability": ensemble_prob,
        "hard_skip": False,
        "hard_skip_reasons": [],
        "forced_flow": False,
        "vetoed": not entered,
        "counter_pass": cp,
        "flags": flags,
    }


def _seed_fade_thesis(
    store: StateStore,
    thesis_id: str,
    symbol: str,
    created: datetime,
    *,
    entered: bool,
    flags: list[str] | None = None,
    counter_pass: dict | None = None,
    outcome: bool | None = None,
    prompt_version: str = "fade-v1",
) -> None:
    """Seed one fade thesis + entry decision into the store."""
    fg = _fade_gate_inputs(
        entered=entered, flags=flags, counter_pass=counter_pass
    )
    store.record_thesis(
        thesis_id=thesis_id,
        bot_id=BOT_FADE,
        symbol=symbol,
        direction="fade",
        stated_probability=fg["stated_probability"],
        mode="paper",
        rationale=f"fade entry for {symbol}",
        created_at=created,
        strategy_bucket="crash:fade",
    )
    action = "buy" if entered else "fade_vetoed"
    store.record_decision(
        decision_id=f"dec-{thesis_id}",
        bot_id=BOT_FADE,
        decision_type="entry",
        action=action,
        inputs={
            "event_id": f"ev-{thesis_id}",
            "symbol": symbol,
            "fade_gate": fg,
        },
        mode="paper",
        as_of=created,
        thesis_id=thesis_id,
        model_id="claude-opus-4-8",
        prompt_version=prompt_version,
    )
    if outcome is not None:
        store.resolve_thesis(thesis_id, outcome)
        store.record_calibration(
            thesis_id=thesis_id,
            bot_id=BOT_FADE,
            trade_type="crash_fade",
            stated_probability=fg["stated_probability"],
            outcome=outcome,
            bucket="medium_confidence",
            strategy_bucket="crash:fade",
        )


def _seed_ride_thesis(
    store: StateStore,
    thesis_id: str,
    symbol: str,
    created: datetime,
    *,
    outcome: bool | None = None,
) -> None:
    store.record_thesis(
        thesis_id=thesis_id,
        bot_id=BOT_FADE,
        symbol=symbol,
        direction="long",
        stated_probability=0.62,
        mode="paper",
        rationale=f"ride entry for {symbol}",
        created_at=created,
        strategy_bucket="earnings:ride",
    )
    store.record_decision(
        decision_id=f"dec-{thesis_id}",
        bot_id=BOT_FADE,
        decision_type="entry",
        action="buy",
        inputs={
            "event_id": f"ev-{thesis_id}",
            "symbol": symbol,
            "quality": {
                "scored": True,
                "quality_score": 0.74,
                "stated_probability": 0.62,
                "flags": [],
                "triage": {"worth_scoring": True, "reason": "beat on revenue and EPS"},
            },
        },
        mode="paper",
        as_of=created,
        thesis_id=thesis_id,
        model_id="claude-opus-4-8",
        prompt_version="qscore-v1",
    )
    if outcome is not None:
        store.resolve_thesis(thesis_id, outcome)
        store.record_calibration(
            thesis_id=thesis_id,
            bot_id=BOT_FADE,
            trade_type="earnings_ride",
            stated_probability=0.62,
            outcome=outcome,
            bucket="medium_confidence",
            strategy_bucket="earnings:ride",
        )


# ---------------------------------------------------------------------------
# 1. debate data extraction — _debate_section (pure HTML helper)
# ---------------------------------------------------------------------------


class TestDebateSection:
    def test_entered_shows_entered_verdict(self) -> None:
        fg = _fade_gate_inputs(entered=True)
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "ENTERED" in html
        assert "VETOED" not in html
        assert "0.58" in html  # final_probability from counter_pass
        assert "Rubric round" in html
        assert "Devil" in html
        assert "Re-read" in html
        assert "Provenance" in html

    def test_vetoed_shows_vetoed_verdict(self) -> None:
        fg = _fade_gate_inputs(entered=False)
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "VETOED" in html
        assert "0.47" in html  # final_probability for the vetoed case

    def test_argument_and_strongest_point_rendered(self) -> None:
        fg = _fade_gate_inputs(entered=True)
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "Structural margin compression" in html  # strongest_point
        assert "structural" in html.lower()              # argument body

    def test_reread_rationale_and_models_in_provenance(self) -> None:
        fg = _fade_gate_inputs(entered=True)
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "gpt-5.5" in html          # counter_model_id
        assert "claude-opus-4-8" in html  # reread_model_id
        assert "fade-v1" in html          # prompt_version

    def test_ensemble_unavailable_shows_degraded_notice(self) -> None:
        fg = _fade_gate_inputs(entered=True, flags=["ensemble_unavailable"])
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "Degraded" in html
        assert "Claude-only" in html or "capped at 0.60" in html

    def test_ensemble_disagreement_noted_in_rubric_round(self) -> None:
        fg = _fade_gate_inputs(entered=True, flags=["ensemble_disagreement"])
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "ensemble_disagreement" in html
        assert "lower number kept" in html.lower() or "diverged" in html.lower()

    def test_per_model_rubric_renders_with_disagreement(self) -> None:
        fg = _fade_gate_inputs(
            entered=True,
            flags=["ensemble_disagreement"],
        )
        fg["model_probabilities"] = {"claude-opus-4-8": 0.70, "gpt-5.5": 0.50}
        fg["model_classifications"] = {
            "claude-opus-4-8": "overreaction",
            "gpt-5.5": "proportionate",
        }
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "Per-model rubric" in html
        assert "Opus" in html
        assert "GPT" in html
        assert "0.70" in html
        assert "0.50" in html
        assert "overreaction" in html
        assert "proportionate" in html
        assert "debate-model-disagree" in html

    def test_old_row_without_per_model_still_shows_ensemble(self) -> None:
        fg = _fade_gate_inputs(entered=True)
        html = _debate_section(fg, prompt_version="fade-v1")
        assert "Ensemble probability" in html
        assert "Per-model rubric" not in html
        assert "not stored in the current schema" not in html

    def test_empty_fg_graceful(self) -> None:
        html = _debate_section({})
        assert "AI Debate" in html
        assert "No fade gate data" in html

    def test_hard_skip_shown(self) -> None:
        fg = {
            "scored": False,
            "hard_skip": True,
            "hard_skip_reasons": ["code:going_concern"],
            "vetoed": False,
            "flags": ["hard_skip_binary_resolution"],
            "counter_pass": {},
        }
        html = _debate_section(fg)
        assert "Hard skip" in html
        assert "going_concern" in html

    def test_triaged_out_shown(self) -> None:
        fg = {
            "scored": False,
            "hard_skip": False,
            "vetoed": False,
            "flags": ["triaged_out"],
            "counter_pass": {},
        }
        html = _debate_section(fg)
        assert "triage" in html.lower()

    def test_counter_pass_error_shown_gracefully(self) -> None:
        fg = {
            "scored": True,
            "classification": "overreaction",
            "ensemble_probability": 0.65,
            "vetoed": True,
            "flags": ["counter_pass_failed"],
            "counter_pass": {"error": "timeout", "reason": "counter_pass_failed", "vetoed": True},
        }
        html = _debate_section(fg)
        assert "VETOED" in html
        assert "pipeline error" in html.lower() or "counter_pass_failed" in html

    def test_v2_reread_fields_shown(self) -> None:
        cp = _make_counter_pass_entered()
        cp["reread_strongest_point"] = "Cost inflation is real."
        cp["reread_rebuttal"] = "But it is already priced in at 2 sigma below mean."
        fg = _fade_gate_inputs(entered=True, counter_pass=cp)
        html = _debate_section(fg, prompt_version="fade-v2")
        assert "Cost inflation is real" in html
        assert "already priced in" in html


# ---------------------------------------------------------------------------
# 2. data layer — fade_debates_index
# ---------------------------------------------------------------------------


class TestFadeDebatesIndex:
    def test_empty_db_returns_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        result = d.fade_debates_index(db)
        assert result == []

    def test_entered_and_vetoed_both_returned(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t-entered", "ACME", base, entered=True)
        _seed_fade_thesis(store, "t-vetoed", "BETA", base, entered=False)
        result = d.fade_debates_index(db)
        assert len(result) == 2
        symbols = {r["symbol"] for r in result}
        assert symbols == {"ACME", "BETA"}

    def test_entered_flag_correct(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t1", "ACME", base, entered=True)
        _seed_fade_thesis(store, "t2", "BETA", base, entered=False)
        result = {r["symbol"]: r for r in d.fade_debates_index(db)}
        assert result["ACME"]["entered"] is True
        assert result["BETA"]["entered"] is False
        assert result["BETA"]["vetoed"] is True

    def test_ride_thesis_not_included(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_ride_thesis(store, "ride-1", "RIDE", base)
        result = d.fade_debates_index(db)
        assert result == []

    def test_outcome_returned_when_resolved(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t1", "ACME", base, entered=True, outcome=True)
        result = d.fade_debates_index(db)
        assert result[0]["outcome"] == 1


# ---------------------------------------------------------------------------
# 3. data layer — scoreboard (v3; replaces gate_scoreboard)
# ---------------------------------------------------------------------------


def _card(sb: dict, strategy_id: str) -> dict:
    for c in sb["strategies"]:
        if c["strategy_id"] == strategy_id:
            return c
    raise KeyError(strategy_id)


class TestGateScoreboard:
    def test_empty_db_returns_zeros(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        sb = d.scoreboard(db)
        ride = _card(sb, "earnings_ride")
        fade = _card(sb, "crash_fade")
        assert ride["gate"]["sample_n"] == 0
        assert ride["status"] == "too_early"
        assert fade["gate"]["sample_n"] == 0

    def test_ride_passes_after_10_samples_good_brier(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        for i in range(10):
            _seed_ride_thesis(store, f"r{i}", f"SYM{i}", base, outcome=True)
        ride = _card(d.scoreboard(db), "earnings_ride")
        assert ride["gate"]["sample_n"] == 10
        assert ride["status"] == "passing"

    def test_ride_fails_with_high_brier(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        for i in range(10):
            _seed_ride_thesis(store, f"r{i}", f"SYM{i}", base, outcome=False)
        ride = _card(d.scoreboard(db), "earnings_ride")
        assert ride["gate"]["sample_n"] == 10
        assert ride["status"] == "failing"
        assert ride["gate"]["value"] is not None
        assert ride["gate"]["value"] > 0.25

    def test_fade_signals_count_entered_and_vetoed(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t1", "ACME", base, entered=True)
        _seed_fade_thesis(store, "t2", "BETA", base, entered=False)
        fade = _card(d.scoreboard(db), "crash_fade")
        # 2 fade-gate evaluations (one entered, one vetoed); the falsifier sample
        # ('sample_n') now counts RESOLVED fades, look-ups live in 'evaluated'.
        assert fade["gate"]["evaluated"] == 2

    def test_fade_gate_includes_net_pl(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t1", "ACME", base, entered=True)
        fade = _card(d.scoreboard(db), "crash_fade")
        assert "net_pl" in fade["gate"]


# ---------------------------------------------------------------------------
# 4. data layer — model_disagreement_stats
# ---------------------------------------------------------------------------


class TestModelDisagreementStats:
    def test_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        stats = d.model_disagreement_stats(db)
        assert stats["total"] == 0
        assert stats["disagree_rate"] is None
        assert stats["unavail_rate"] is None

    def test_counts_disagreements(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t1", "A", base, entered=True, flags=["ensemble_disagreement"])
        _seed_fade_thesis(store, "t2", "B", base, entered=True, flags=[])
        _seed_fade_thesis(store, "t3", "C", base, entered=True, flags=["ensemble_unavailable"])
        stats = d.model_disagreement_stats(db)
        assert stats["total"] == 3
        assert stats["disagreements"] == 1
        assert stats["ensemble_unavailable"] == 1
        assert abs(stats["disagree_rate"] - 1 / 3) < 1e-9


# ---------------------------------------------------------------------------
# 5. data layer — veto_value_tally
# ---------------------------------------------------------------------------


class TestVetoValueTally:
    def test_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        tally = d.veto_value_tally(db)
        assert tally["total_vetoed"] == 0
        assert tally["total_resolved"] == 0
        assert tally["meaningful"] is False
        assert tally["by_version"] == {}

    def test_unresolved_vetoes_tracked(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "v1", "A", base, entered=False, prompt_version="fade-v1")
        _seed_fade_thesis(store, "v2", "B", base, entered=False, prompt_version="fade-v2")
        tally = d.veto_value_tally(db)
        assert tally["total_vetoed"] == 2
        assert tally["total_resolved"] == 0
        assert tally["meaningful"] is False
        assert "fade-v1" in tally["by_version"]
        assert "fade-v2" in tally["by_version"]

    def test_resolved_vetoes_win_counted(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        # Seed a vetoed fade; then resolve its thesis as a win (would have won)
        _seed_fade_thesis(store, "v1", "A", base, entered=False, outcome=True)
        tally = d.veto_value_tally(db)
        assert tally["total_vetoed"] == 1
        assert tally["total_resolved"] == 1
        assert tally["by_version"]["fade-v1"]["would_have_won"] == 1


# ---------------------------------------------------------------------------
# 6. render_thesis_page — debate section + edge cases
# ---------------------------------------------------------------------------


class TestRenderThesisPageDebate:
    def test_entered_fade_shows_debate(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t-entered", "ACME", base, entered=True)
        page = render_thesis_page(db, "t-entered")
        assert "AI Debate" in page
        assert "Rubric round" in page
        assert "Devil" in page
        assert "ENTERED" in page
        assert "fade-v1" in page

    def test_vetoed_fade_shows_vetoed(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t-vetoed", "BETA", base, entered=False)
        page = render_thesis_page(db, "t-vetoed")
        assert "AI Debate" in page
        assert "VETOED" in page

    def test_ride_thesis_no_debate_block(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_ride_thesis(store, "r1", "RIDE", base)
        page = render_thesis_page(db, "r1")
        # Ride theses show quality section, not AI Debate
        assert "Quality assessment" in page
        # Should NOT show debate blocks
        assert "Rubric round" not in page

    def test_old_row_missing_counter_pass_no_crash(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        # Row with minimal fade_gate, no counter_pass
        store.record_thesis(
            thesis_id="old-row",
            bot_id=BOT_FADE,
            symbol="OLD",
            direction="fade",
            stated_probability=0.61,
            mode="paper",
            rationale="old fade",
            created_at=base,
            strategy_bucket="crash:fade",
        )
        store.record_decision(
            decision_id="dec-old",
            bot_id=BOT_FADE,
            decision_type="entry",
            action="buy",
            inputs={
                "symbol": "OLD",
                "fade_gate": {"scored": True, "classification": "overreaction",
                               "ensemble_probability": 0.61, "vetoed": False,
                               "flags": [], "counter_pass": {}},
            },
            mode="paper",
            as_of=base,
            thesis_id="old-row",
            prompt_version="fade-v1",
        )
        page = render_thesis_page(db, "old-row")
        assert "AI Debate" in page
        # Should not crash even with empty counter_pass

    def test_degraded_ensemble_unavailable_shown(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(
            store, "t-deg", "DEG", base,
            entered=True, flags=["ensemble_unavailable"],
        )
        page = render_thesis_page(db, "t-deg")
        assert "Degraded" in page or "unavailable" in page.lower()

    def test_mech_fade_no_fg_data(self, tmp_path: Path) -> None:
        """Mechanical twin lane: strategy_bucket=crash:fade but no fade_gate inputs."""
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        store.record_thesis(
            thesis_id="mech-fade",
            bot_id=BOT_MECH,
            symbol="MECH",
            direction="fade",
            stated_probability=0.55,
            mode="paper",
            rationale="mechanical fade",
            created_at=base,
            strategy_bucket="crash:fade",
        )
        store.record_decision(
            decision_id="dec-mech",
            bot_id=BOT_MECH,
            decision_type="entry",
            action="buy",
            inputs={"symbol": "MECH", "event_id": "ev-mech"},
            mode="paper",
            as_of=base,
            thesis_id="mech-fade",
        )
        page = render_thesis_page(db, "mech-fade")
        # Should show the "no AI gate data" fallback, not crash
        assert "AI Debate" in page
        assert "mechanical twin" in page.lower() or "pre-dates" in page.lower()

    def test_missing_thesis_returns_not_found(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_thesis_page(db, "nonexistent-id")
        assert "not found" in page.lower()


# ---------------------------------------------------------------------------
# 7. render_debates_page
# ---------------------------------------------------------------------------


class TestRenderDebatesPage:
    def test_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_debates_page(db)
        assert "ai debates" in page.lower()
        assert "No fade decisions" in page

    def test_shows_entered_and_vetoed(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t1", "ACME", base, entered=True)
        _seed_fade_thesis(store, "t2", "BETA", base, entered=False)
        page = render_debates_page(db)
        assert "ACME" in page
        assert "BETA" in page
        assert "ENTERED" in page
        assert "VETOED" in page

    def test_link_to_thesis_present(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t-linked", "LINK", base, entered=True)
        page = render_debates_page(db)
        assert "/thesis/t-linked" in page
        assert "view debate" in page


# ---------------------------------------------------------------------------
# 8. gate scoreboard panel in render_page
# ---------------------------------------------------------------------------


class TestGateScoreboardPanel:
    def test_renders_on_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_page(
            db, assumed_slippage_pct=0.003, ops_db_path=db,
            logs_dir=tmp_path / "logs",
        )
        assert "sb-card" in page or "too early" in page
        assert "/strategy/earnings_ride" in page

    def test_renders_with_populated_ledger(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        for i in range(10):
            _seed_ride_thesis(store, f"r{i}", f"SYM{i}", base, outcome=True)
        _seed_fade_thesis(store, "f1", "FADE1", base, entered=True)
        page = render_page(
            db, assumed_slippage_pct=0.003, ops_db_path=db,
            logs_dir=tmp_path / "logs",
        )
        assert "passing" in page.lower()
        assert "1 / 40" in page or "too early" in page


class TestModelDisagreementPanel:
    def test_renders_on_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_strategy_page(
            db, "crash_fade", assumed_slippage_pct=0.003, ops_db_path=db,
            logs_dir=tmp_path / "logs",
        )
        assert "AI-value trackers" in page
        assert "No fade decisions yet" in page

    def test_renders_with_disagreement_data(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        store = StateStore(db)
        base = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
        _seed_fade_thesis(store, "t1", "A", base, entered=True,
                          flags=["ensemble_disagreement"])
        _seed_fade_thesis(store, "t2", "B", base, entered=True, flags=[])
        page = render_strategy_page(
            db, "crash_fade", assumed_slippage_pct=0.003, ops_db_path=db,
            logs_dir=tmp_path / "logs",
        )
        assert "Fade decisions analysed" in page or "disagreement" in page.lower()


class TestRegressionGuard:
    def test_render_page_has_v3_ia(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_page(
            db, assumed_slippage_pct=0.003, ops_db_path=db,
            logs_dir=tmp_path / "logs",
        )
        for marker in [
            "ops-strip",
            "nav-header",
            "/execution",
            "/ops",
            "/lens",
        ]:
            assert marker in page, f"Expected '{marker}' not found in rendered page"

    def test_debates_link_in_nav(self, tmp_path: Path) -> None:
        db = tmp_path / "paper.db"
        StateStore(db)
        page = render_page(
            db, assumed_slippage_pct=0.003, ops_db_path=db,
            logs_dir=tmp_path / "logs",
        )
        assert "href='/debates'" in page or 'href="/debates"' in page

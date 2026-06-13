"""Shot 5 — tickets 2.5a (crash detection + source doc), 2.5b (fade rubric),
and 2.5c (fade entries/exits in the strategy class).

All offline: EDGAR/Tiingo via seeded CachedHTTP caches, LLMs via StubLLMClient.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.proportionality.crash import (
    CrashEvent,
    LiveCrashDetector,
    SourceDoc,
    detect_crashes_from_prices_csv,
    detect_crashes_in_series,
    find_crash_source_doc,
    load_band_symbols,
)
from stockbot.bots.proportionality.fade import (
    FADE_PROBABILITY_ENTRY_THRESHOLD,
    CrashFadeGate,
    FadeAssessment,
    assess_crash_event,
    binary_resolution_matches,
)
from stockbot.bots.proportionality.ride import (
    BOT_ID,
    ProportionalityRideStrategy,
    RideConfig,
)
from stockbot.config import AppConfig, SimulatedBrokerConfig
from stockbot.core.llm import ModelTier, StubLLMClient, build_router
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.data.real_loader import (
    SEC_SUBMISSIONS_URL,
    CachedHTTP,
    cache_file_path,
)
from stockbot.harness.loop import drive_strategy
from stockbot.types import ExecutionMode

UTC = timezone.utc
D0 = datetime(2026, 3, 2, 20, 0, tzinfo=UTC)
CIK = 1234567


def _series(closes: list[float], start: datetime = D0) -> list[tuple[datetime, float]]:
    return [(start + timedelta(days=i), c) for i, c in enumerate(closes)]


# ---------------------------------------------------------------------------
# 2.5a — detection mechanics
# ---------------------------------------------------------------------------


class TestCrashDetection:
    def test_detects_close_to_close_drop_of_10_percent_or_more(self) -> None:
        crashes = detect_crashes_in_series(_series([100.0, 101.0, 89.0, 90.0]))
        assert len(crashes) == 1
        ts, prev, close, drop = crashes[0]
        assert prev == 101.0 and close == 89.0
        assert drop == pytest.approx((89.0 - 101.0) / 101.0)

    def test_nine_percent_drop_does_not_fire(self) -> None:
        assert detect_crashes_in_series(_series([100.0, 91.0])) == []

    def test_exactly_ten_percent_fires(self) -> None:
        assert len(detect_crashes_in_series(_series([100.0, 90.0]))) == 1

    def test_bad_rows_never_fabricate_drops(self) -> None:
        # A zero/negative close breaks the chain instead of producing a crash.
        assert detect_crashes_in_series(_series([100.0, 0.0, 50.0])) == []

    def test_band_filter_from_universe_metadata(self, tmp_path: Path) -> None:
        meta = {
            "symbols": [
                {"symbol": "INBAND", "market_cap": 800e6, "median_dollar_volume": 2_000_000},
                {"symbol": "THINADV", "market_cap": 800e6, "median_dollar_volume": 700_000},
                {"symbol": "TOOBIG", "market_cap": 12e9, "median_dollar_volume": 9_000_000},
                {"symbol": "TOOSMALL", "market_cap": 200e6, "median_dollar_volume": 2_000_000},
                {"symbol": "MIDCAP", "market_cap": 5e9, "median_dollar_volume": 3_000_000},
            ]
        }
        p = tmp_path / "meta.json"
        p.write_text(json.dumps(meta), encoding="utf-8")
        band = load_band_symbols(p)
        # $300M-$10B and >$1M ADV: MIDCAP ($5B) is in the 2.5a band even though
        # the screener's own cap band stopped at $3B.
        assert set(band) == {"INBAND", "MIDCAP"}
        assert band["INBAND"]["market_cap"] == 800e6

    def test_csv_detection_is_band_filtered(self, tmp_path: Path) -> None:
        rows = ["timestamp,symbol,price"]
        for i, px in enumerate([100.0, 85.0]):
            ts = (D0 + timedelta(days=i)).isoformat()
            rows.append(f"{ts},CRSH,{px}")
            rows.append(f"{ts},OOBND,{px}")
        csv_path = tmp_path / "prices.csv"
        csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        band = {"CRSH": {"market_cap": 1e9, "median_dollar_volume": 2e6}}
        crashes = detect_crashes_from_prices_csv(csv_path, band)
        assert [c.symbol for c in crashes] == ["CRSH"]
        ev = crashes[0]
        assert ev.event_id == f"CRSH-crash-{(D0 + timedelta(days=1)).date().isoformat()}"
        assert ev.drop_pct == pytest.approx(-0.15)
        assert ev.market_cap == 1e9


# ---------------------------------------------------------------------------
# 2.5a — source-document selection
# ---------------------------------------------------------------------------


def _seed_submissions(cache_dir: Path, forms: list[tuple[str, str, str, str]]) -> None:
    """forms: (form, accession, filingDate, primaryDocument)."""
    payload = {
        "filings": {
            "recent": {
                "form": [f[0] for f in forms],
                "accessionNumber": [f[1] for f in forms],
                "filingDate": [f[2] for f in forms],
                "primaryDocument": [f[3] for f in forms],
            }
        }
    }
    cache_file_path(cache_dir, SEC_SUBMISSIONS_URL.format(cik=CIK)).write_text(
        json.dumps(payload), encoding="utf-8"
    )


class TestSourceDocSelection:
    def test_current_report_beats_periodic_filing(self, tmp_path: Path) -> None:
        _seed_submissions(
            tmp_path,
            [
                ("10-Q", "acc-10q", "2026-03-02", "q.htm"),
                ("8-K", "acc-8k", "2026-03-01", "k.htm"),
                ("4", "acc-form4", "2026-03-02", "f4.xml"),
            ],
        )
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        doc = find_crash_source_doc(
            client, cik=CIK, crash_date=D0.date(), sec_user_agent="t t@e.com"
        )
        assert doc is not None and doc.form == "8-K" and doc.accession_number == "acc-8k"

    def test_window_excludes_stale_and_far_future_filings(self, tmp_path: Path) -> None:
        _seed_submissions(
            tmp_path,
            [
                ("8-K", "acc-old", "2026-02-20", "old.htm"),   # > 3 days before
                ("8-K", "acc-late", "2026-03-05", "late.htm"),  # > 1 day after
            ],
        )
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        assert (
            find_crash_source_doc(client, cik=CIK, crash_date=D0.date(), sec_user_agent="t t@e.com")
            is None
        )

    def test_next_morning_8k_is_accepted(self, tmp_path: Path) -> None:
        _seed_submissions(tmp_path, [("8-K", "acc-nextday", "2026-03-03", "k.htm")])
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        doc = find_crash_source_doc(
            client, cik=CIK, crash_date=D0.date(), sec_user_agent="t t@e.com"
        )
        assert doc is not None and doc.accession_number == "acc-nextday"

    def test_closest_filing_wins_within_a_class(self, tmp_path: Path) -> None:
        _seed_submissions(
            tmp_path,
            [
                ("8-K", "acc-3daysago", "2026-02-27", "a.htm"),
                ("8-K", "acc-sameday", "2026-03-02", "b.htm"),
            ],
        )
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        doc = find_crash_source_doc(
            client, cik=CIK, crash_date=D0.date(), sec_user_agent="t t@e.com"
        )
        assert doc is not None and doc.accession_number == "acc-sameday"

    def test_offering_prospectus_selected_when_no_current_report(self, tmp_path: Path) -> None:
        _seed_submissions(
            tmp_path,
            [
                ("424B5", "acc-424", "2026-03-02", "p.htm"),
                ("10-K", "acc-10k", "2026-03-02", "k10.htm"),
            ],
        )
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        doc = find_crash_source_doc(
            client, cik=CIK, crash_date=D0.date(), sec_user_agent="t t@e.com"
        )
        assert doc is not None and doc.form == "424B5"


# ---------------------------------------------------------------------------
# 2.5a — live detector (offline IEX fixture)
# ---------------------------------------------------------------------------


class TestLiveCrashDetector:
    def _client(self, tmp_path: Path, rows: list[dict]) -> CachedHTTP:
        from stockbot.bots.proportionality.crash import TIINGO_IEX_URL

        cache = CachedHTTP(cache_dir=tmp_path, offline=True)
        tickers = ",".join(sorted({r["ticker"] for r in rows}))
        cache_file_path(
            tmp_path, TIINGO_IEX_URL, {"tickers": tickers, "token": "k"}
        ).write_text(json.dumps(rows), encoding="utf-8")
        return cache

    def test_detects_crash_and_caches_per_day(self, tmp_path: Path) -> None:
        band = {
            "DOWN": {"market_cap": 1e9, "median_dollar_volume": 2e6},
            "FLAT": {"market_cap": 1e9, "median_dollar_volume": 2e6},
        }
        client = self._client(
            tmp_path,
            [
                {"ticker": "DOWN", "prevClose": 50.0, "tngoLast": 42.0},
                {"ticker": "FLAT", "prevClose": 50.0, "tngoLast": 49.5},
            ],
        )
        det = LiveCrashDetector(
            band=band, client=client, tiingo_api_key="k", sec_user_agent="t t@e.com"
        )
        crashes = det.detect(D0)
        assert [c.symbol for c in crashes] == ["DOWN"]
        assert crashes[0].drop_pct == pytest.approx(-0.16)
        assert crashes[0].event_id == f"DOWN-crash-{D0.date().isoformat()}"
        # Second call same day: identical result from the per-day cache.
        assert det.detect(D0) is crashes


# ---------------------------------------------------------------------------
# 2.5b — the rubric pipeline (stubbed models)
# ---------------------------------------------------------------------------

_TRIAGE_YES = '{"worth_scoring": true, "reason": "guidance cut disclosed"}'


def _rubric_json(
    classification: str = "overreaction",
    prob: float = 0.70,
    binary: bool = False,
    forced: bool = False,
) -> str:
    return json.dumps(
        {
            "fundamental_dollar_delta_usd": -40e6,
            "anchors": ["guidance cut x 5x multiple"],
            "classification": classification,
            "probability_reversion_10d": prob,
            "binary_resolution": binary,
            "forced_flow": forced,
            "rationale": "market-cap loss far exceeds the guided delta",
        }
    )


def _reread_json(prob: float) -> str:
    return json.dumps({"probability_reversion_10d": prob, "rationale": "re-read"})


_COUNTER_JSON = json.dumps(
    {"argument": "the guidance cut implies durable margin damage", "strongest_point": "margins"}
)

_DOC = (
    "The Company announced preliminary results and reduced its full-year revenue "
    "guidance by $8 million, citing temporary channel destocking. " * 20
)


def _event() -> CrashEvent:
    return CrashEvent(
        event_id="CRSH-crash-2026-03-02",
        symbol="CRSH",
        as_of=D0,
        prev_close=50.0,
        close=42.0,
        drop_pct=-0.16,
        market_cap=1e9,
        median_dollar_volume=2e6,
        source_doc=SourceDoc(
            form="8-K", accession_number="0000000000-26-000001",
            filed_at="2026-03-02", primary_document="k.htm", cik=CIK,
        ),
    )


def _router(
    *,
    strong: list[tuple[str, str]],
    cross: list[tuple[str, str]] | None,
    strong_default: str = "{}",
    cross_default: str = "{}",
):
    clients = {
        ModelTier.TRIAGE: StubLLMClient("haiku-stub", default=_TRIAGE_YES),
        ModelTier.STRONG: StubLLMClient("opus-stub", strong, default=strong_default),
    }
    if cross is not None:
        clients[ModelTier.CROSS_CHECK] = StubLLMClient("gpt-stub", cross, default=cross_default)
    return build_router(cap_usd=100.0, clients=clients)


class TestFadeRubric:
    def test_code_hard_skip_fires_before_any_llm_call(self) -> None:
        router = _router(strong=[], cross=[])
        doc = "The audit report expresses substantial doubt about the Company's ability to continue as a going concern. " * 10
        out = assess_crash_event(router, event=_event(), filing_text=doc)
        assert out.hard_skip and "code:going_concern" in out.hard_skip_reasons
        assert not out.passes_entry_threshold
        assert router.tracker.calls == 0  # skipped in code, zero spend

    def test_binary_resolution_regexes(self) -> None:
        assert binary_resolution_matches("jury verdict against the company") == ["trial_verdict"]
        assert binary_resolution_matches("filed a voluntary Chapter 11 petition") == ["bankruptcy"]
        assert binary_resolution_matches("revenue guidance lowered") == []

    def test_llm_binary_resolution_flag_is_enforced_in_code(self) -> None:
        router = _router(
            strong=[], cross=[],
            strong_default=_rubric_json(binary=True, prob=0.80),
            cross_default=_rubric_json(binary=False, prob=0.80),
        )
        out = assess_crash_event(router, event=_event(), filing_text=_DOC)
        assert out.hard_skip and "llm:binary_resolution" in out.hard_skip_reasons
        assert not out.passes_entry_threshold

    def test_agreement_runs_counter_pass_and_enters(self) -> None:
        router = _router(
            strong=[("YOUR ORIGINAL ASSESSMENT", _reread_json(0.66))],
            cross=[("ARGUE THE CRASH IS CORRECT", _COUNTER_JSON)],
            strong_default=_rubric_json(prob=0.72),
            cross_default=_rubric_json(prob=0.68),
        )
        out = assess_crash_event(router, event=_event(), filing_text=_DOC)
        assert out.scored and out.classification == "overreaction"
        assert out.ensemble_probability == pytest.approx(0.70)
        # Final = min(ensemble, revised) = 0.66; above threshold -> no veto.
        assert out.stated_probability == pytest.approx(0.66)
        assert not out.vetoed and out.passes_entry_threshold
        assert out.counter_pass["revised_probability"] == pytest.approx(0.66)
        assert out.counter_pass["vetoed"] is False

    def test_counter_pass_veto(self) -> None:
        router = _router(
            strong=[("YOUR ORIGINAL ASSESSMENT", _reread_json(0.40))],
            cross=[("ARGUE THE CRASH IS CORRECT", _COUNTER_JSON)],
            strong_default=_rubric_json(prob=0.72),
            cross_default=_rubric_json(prob=0.70),
        )
        out = assess_crash_event(router, event=_event(), filing_text=_DOC)
        assert out.vetoed and "counter_pass_veto" in out.flags
        assert out.stated_probability == pytest.approx(0.40)
        assert not out.passes_entry_threshold
        assert out.counter_pass["vetoed"] is True

    def test_classification_disagreement_blocks_the_fade(self) -> None:
        router = _router(
            strong=[], cross=[],
            strong_default=_rubric_json(classification="overreaction", prob=0.70),
            cross_default=_rubric_json(classification="proportionate", prob=0.70),
        )
        out = assess_crash_event(router, event=_event(), filing_text=_DOC)
        assert "classification_disagreement" in out.flags
        assert out.classification == "proportionate"
        assert not out.passes_entry_threshold
        # No counter-pass spend on a non-candidate.
        assert out.counter_pass == {}

    def test_probability_disagreement_caps_at_lower(self) -> None:
        router = _router(
            strong=[("YOUR ORIGINAL ASSESSMENT", _reread_json(0.95))],
            cross=[("ARGUE THE CRASH IS CORRECT", _COUNTER_JSON)],
            strong_default=_rubric_json(prob=0.85),
            cross_default=_rubric_json(prob=0.60),
        )
        out = assess_crash_event(router, event=_event(), filing_text=_DOC)
        assert "ensemble_disagreement" in out.flags
        assert out.ensemble_probability == pytest.approx(0.60)
        # Re-read can't raise the final above the ensemble cap.
        assert out.stated_probability == pytest.approx(0.60)

    def test_degraded_mode_caps_and_flags_and_counter_passes_same_model(self) -> None:
        router = _router(
            strong=[
                ("YOUR ORIGINAL ASSESSMENT", _reread_json(0.58)),
                ("ARGUE THE CRASH IS CORRECT", _COUNTER_JSON),
            ],
            cross=None,
            strong_default=_rubric_json(prob=0.80),
        )
        out = assess_crash_event(router, event=_event(), filing_text=_DOC)
        assert "ensemble_unavailable" in out.flags
        assert out.ensemble_probability == pytest.approx(0.60)  # degradation cap
        assert "counter_pass_same_model" in out.flags
        assert out.stated_probability == pytest.approx(0.58)
        assert out.passes_entry_threshold

    def test_threshold_is_the_preregistered_constant(self) -> None:
        assert FADE_PROBABILITY_ENTRY_THRESHOLD == 0.55


class TestCrashFadeGate:
    def test_no_source_doc_is_flagged_not_scored(self, tmp_path: Path) -> None:
        router = _router(strong=[], cross=[])
        gate = CrashFadeGate(router, CachedHTTP(cache_dir=tmp_path, offline=True), "t t@e.com")
        ev = CrashEvent(
            event_id="NODOC-crash-2026-03-02", symbol="NODOC", as_of=D0,
            prev_close=50.0, close=42.0, drop_pct=-0.16,
        )
        out = gate(ev)
        assert not out.scored and "no_source_doc" in out.flags
        assert not out.passes_entry_threshold

    def test_assessments_are_cached_by_event_id(self, tmp_path: Path) -> None:
        router = _router(strong=[], cross=[])
        gate = CrashFadeGate(router, CachedHTTP(cache_dir=tmp_path, offline=True), "t t@e.com")
        ev = CrashEvent(
            event_id="NODOC-crash-2026-03-02", symbol="NODOC", as_of=D0,
            prev_close=50.0, close=42.0, drop_pct=-0.16,
        )
        assert gate(ev) is gate(ev)


# ---------------------------------------------------------------------------
# 2.5c — fade entries/exits in the strategy class (offline end-to-end)
# ---------------------------------------------------------------------------

START = datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
CRASH_DAY = 2  # the bar index on which the synthetic crash is "detected"


def _write_fade_dataset(
    data_dir: Path, prices: list[float], symbols: list[str] | None = None
) -> None:
    """Identical price path per symbol, no earnings events — fades only."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume"])
        w.writeheader()
        for day, price in enumerate(prices):
            for symbol in symbols or ["CRSH"]:
                w.writerow({
                    "timestamp": (START + timedelta(days=day)).isoformat(),
                    "symbol": symbol, "price": f"{price:.4f}",
                    "bid": "", "ask": "", "volume": 200_000,
                })
    with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"])
        w.writeheader()


def _fade_cfg(data_dir: Path, db_path: Path) -> AppConfig:
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
        initial_cash=100_000.0, execution_timing="next_bar", slippage_pct=0.0,
        cost_profile="flat",
    )
    return cfg


def _synthetic_detector(symbols: list[str] | None = None):
    """Returns the crash list ONLY on the crash day (like the live detector)."""
    crash_date = (START + timedelta(days=CRASH_DAY)).date()

    def detect(now: datetime) -> list[CrashEvent]:
        if now.date() != crash_date:
            return []
        return [
            CrashEvent(
                event_id=f"{s}-crash-{crash_date.isoformat()}",
                symbol=s, as_of=now, prev_close=100.0, close=85.0,
                drop_pct=-0.15, market_cap=1e9, median_dollar_volume=2e6,
            )
            for s in (symbols or ["CRSH"])
        ]

    return detect


def _passing_assessment(prob: float = 0.66) -> FadeAssessment:
    return FadeAssessment(
        event_id=f"CRSH-crash-{(START + timedelta(days=CRASH_DAY)).date().isoformat()}",
        symbol="CRSH", scored=True, classification="overreaction",
        stated_probability=prob, ensemble_probability=0.70,
        counter_pass={"revised_probability": prob, "final_probability": prob, "vetoed": False},
        rationale="cap loss >> guided delta",
        model_probabilities={"claude-opus-4-8": 0.70, "gpt-5.5": 0.50},
        model_classifications={"claude-opus-4-8": "overreaction", "gpt-5.5": "proportionate"},
        flags=["ensemble_disagreement"],
    )


def _vetoed_assessment() -> FadeAssessment:
    return FadeAssessment(
        event_id=f"CRSH-crash-{(START + timedelta(days=CRASH_DAY)).date().isoformat()}",
        symbol="CRSH", scored=True, classification="overreaction",
        stated_probability=0.40, ensemble_probability=0.70, vetoed=True,
        counter_pass={"revised_probability": 0.40, "final_probability": 0.40, "vetoed": True},
        flags=["counter_pass_veto"],
    )


_FADE_ON = RideConfig(ride_enabled=False, fade_enabled=True)


def _run_fade(tmp_path: Path, prices: list[float], *, ride: RideConfig = _FADE_ON,
              detector=None, fade_gate=None, symbols: list[str] | None = None) -> StateStore:
    data_dir = tmp_path / "data"
    _write_fade_dataset(data_dir, prices, symbols)
    db_path = tmp_path / "state.db"
    cfg = _fade_cfg(data_dir, db_path)

    def factory(_comp) -> Strategy:
        return ProportionalityRideStrategy(
            ride, crash_detector=detector or _synthetic_detector(), fade_gate=fade_gate,
        )

    drive_strategy(factory, cfg)
    return StateStore(db_path)


class TestFadeStrategy:
    def test_graded_overreaction_enters_and_time_stops(self, tmp_path: Path) -> None:
        # Crash detected day 2, entry fills day 3 (next bar) at 85; flat after:
        # only the 10-day time-stop can fire.
        prices = [100.0, 100.0, 85.0] + [85.0] * 14
        store = _run_fade(tmp_path, prices, fade_gate=lambda ev: _passing_assessment())

        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        assert [d["action"] for d in entries] == ["fade_buy"]
        assert entries[0]["inputs"]["fade_gate"]["counter_pass"]["vetoed"] is False
        assert entries[0]["inputs"]["strategy_bucket"] == "crash:fade"
        fg = entries[0]["inputs"]["fade_gate"]
        assert fg["model_probabilities"] == {
            "claude-opus-4-8": 0.70,
            "gpt-5.5": 0.50,
        }
        assert fg["model_classifications"] == {
            "claude-opus-4-8": "overreaction",
            "gpt-5.5": "proportionate",
        }

        exits = [d["action"] for d in store.get_decisions(BOT_ID, "backtest", "exit")]
        assert exits.count("fade_time_stop") == 1
        assert "fade_hard_stop" not in exits

        resolutions = store.get_decisions(BOT_ID, "backtest", "resolution")
        assert len(resolutions) == 1
        assert resolutions[0]["inputs"]["strategy_bucket"] == "crash:fade"
        assert resolutions[0]["inputs"]["trade_kind"] == "fade"

        with store.transaction() as conn:
            theses = conn.execute(
                "SELECT stated_probability, strategy_bucket FROM theses"
            ).fetchall()
            cal = conn.execute(
                "SELECT strategy_bucket, stated_probability, trade_type FROM calibration_ledger"
            ).fetchall()
        assert len(theses) == 1
        assert theses[0]["stated_probability"] == pytest.approx(0.66)
        assert theses[0]["strategy_bucket"] == "crash:fade"
        assert cal[0]["strategy_bucket"] == "crash:fade"
        assert cal[0]["stated_probability"] == pytest.approx(0.66)
        assert cal[0]["trade_type"] == "crash_fade"

    def test_hard_stop_cuts_the_fade(self, tmp_path: Path) -> None:
        # Entry fills day 3 at 85; day 4 drops to 78 (-8.2% from entry).
        prices = [100.0, 100.0, 85.0, 85.0, 78.0, 78.0, 78.0]
        store = _run_fade(tmp_path, prices, fade_gate=lambda ev: _passing_assessment())
        exits = [d["action"] for d in store.get_decisions(BOT_ID, "backtest", "exit")]
        assert "fade_hard_stop" in exits
        resolutions = store.get_decisions(BOT_ID, "backtest", "resolution")
        assert resolutions[0]["action"] == "loss"

    def test_veto_is_logged_and_blocks_the_entry(self, tmp_path: Path) -> None:
        prices = [100.0, 100.0, 85.0, 85.0, 85.0]
        store = _run_fade(tmp_path, prices, fade_gate=lambda ev: _vetoed_assessment())
        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        assert [d["action"] for d in entries] == ["fade_vetoed"]
        # Hard rule 3: the counter-pass result is on the decision row.
        assert entries[0]["inputs"]["fade_gate"]["counter_pass"]["vetoed"] is True
        assert store.get_fills(BOT_ID, "backtest") == []

    def test_mech_twin_fades_every_crash_at_constant_probability(self, tmp_path: Path) -> None:
        prices = [100.0, 100.0, 85.0] + [85.0] * 14
        store = _run_fade(tmp_path, prices, fade_gate=None)  # the coin
        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        assert [d["action"] for d in entries] == ["fade_buy"]
        with store.transaction() as conn:
            theses = conn.execute("SELECT stated_probability FROM theses").fetchall()
        assert theses[0]["stated_probability"] == pytest.approx(0.55)

    def test_position_sized_at_three_percent_of_equity(self, tmp_path: Path) -> None:
        prices = [100.0, 100.0, 85.0] + [85.0] * 14
        store = _run_fade(tmp_path, prices, fade_gate=None)
        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        inputs = entries[0]["inputs"]
        # 3% of $100k equity at the 85.0 quote.
        assert inputs["quantity"] == pytest.approx(100_000.0 * 0.03 / 85.0, rel=1e-3)

    def test_max_two_concurrent_fades(self, tmp_path: Path) -> None:
        # Three same-day crashes, all priced in the feed: only the first two
        # may enter (pre-registered max 2 concurrent fades).
        prices = [100.0, 100.0, 85.0] + [85.0] * 14
        names = ["AAAA", "BBBB", "CCCC"]
        detector = _synthetic_detector(names)
        store = _run_fade(
            tmp_path, prices, detector=detector, fade_gate=None, symbols=names,
        )
        buys = [f for f in store.get_fills(BOT_ID, "backtest") if f["side"] == "buy"]
        assert len(buys) == 2
        assert sorted(f["symbol"] for f in buys) == ["AAAA", "BBBB"]

    def test_fade_disabled_by_default_is_bit_identical(self, tmp_path: Path) -> None:
        # Default RideConfig: detector present but fade_enabled OFF -> nothing.
        prices = [100.0, 100.0, 85.0, 85.0, 85.0]
        store = _run_fade(
            tmp_path, prices, ride=RideConfig(), fade_gate=lambda ev: _passing_assessment(),
        )
        assert store.get_decisions(BOT_ID, "backtest", "entry") == []
        assert store.get_fills(BOT_ID, "backtest") == []

    def test_state_round_trip_preserves_trade_kind(self) -> None:
        strat = ProportionalityRideStrategy(_FADE_ON)
        strat.restore_state({
            "surprise_history": [],
            "open": [{
                "symbol": "CRSH", "thesis_id": "t1", "entry_order_id": "o1",
                "entry_ts": START.isoformat(), "event_id": "e1",
                "stated_probability": 0.66, "trade_kind": "fade",
            }],
            "closing": [],
        })
        exported = strat.export_state()
        assert exported["open"][0]["trade_kind"] == "fade"
        # Legacy rows without the field restore as rides.
        strat.restore_state({
            "surprise_history": [],
            "open": [{
                "symbol": "AAA", "thesis_id": "t2", "entry_order_id": "o2",
                "entry_ts": START.isoformat(), "event_id": "e2",
            }],
            "closing": [],
        })
        assert strat.export_state()["open"][0]["trade_kind"] == "ride"

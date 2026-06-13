"""Shot 11 — fade counter-pass v2 (anti-sycophancy), run as a parallel
champion/challenger alongside v1.

All offline: LLMs via StubLLMClient, no network. Proves:
- the v2 prompt constants are frozen/pre-registered and the v2 re-read prompt
  OMITS the prior probability and FORCES strongest-point + rebuttal before the
  number;
- the rubric ensemble runs ONCE per crash and is shared across versions (no
  double ensemble spend);
- fade-v1 is bit-identical whether or not v2 runs;
- the crash_fade_v2 lane enters/vetoes on v2's decision and books the
  crash:fade_v2 bucket, with the counter-pass logged on every decision.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.proportionality.crash import CrashEvent, SourceDoc
from stockbot.bots.proportionality.fade import (
    FADE_PROBABILITY_ENTRY_THRESHOLD,
    PROMPT_VERSION,
    PROMPT_VERSION_V2,
    FadeAssessment,
    SharedFadeGate,
    assess_crash_event,
    assess_crash_event_versions,
)
from stockbot.bots.proportionality.ride import (
    BOT_ID,
    ProportionalityRideStrategy,
    RideConfig,
)
from stockbot.config import AppConfig, SimulatedBrokerConfig
from stockbot.core.llm import LLMRouter, ModelTier, SpendTracker, StubLLMClient
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.data.real_loader import CachedHTTP
from stockbot.harness.loop import drive_strategy
from stockbot.types import ExecutionMode

UTC = timezone.utc
D0 = datetime(2026, 3, 2, 20, 0, tzinfo=UTC)
CIK = 1234567

_TRIAGE_YES = '{"worth_scoring": true, "reason": "guidance cut disclosed"}'

_DOC = (
    "The Company announced preliminary results and reduced its full-year revenue "
    "guidance by $8 million, citing temporary channel destocking. " * 20
)


def _rubric_json(classification: str = "overreaction", prob: float = 0.70) -> str:
    return json.dumps(
        {
            "fundamental_dollar_delta_usd": -40e6,
            "anchors": ["guidance cut x 5x multiple"],
            "classification": classification,
            "probability_reversion_10d": prob,
            "binary_resolution": False,
            "forced_flow": False,
            "rationale": "market-cap loss far exceeds the guided delta",
        }
    )


_COUNTER_V1_JSON = json.dumps(
    {"argument": "the guidance cut implies durable margin damage", "strongest_point": "margins"}
)
_COUNTER_V2_JSON = json.dumps(
    {"argument": "v2: the destocking is structural, not temporary", "strongest_point": "structural"}
)


def _reread_v1_json(prob: float) -> str:
    return json.dumps({"probability_reversion_10d": prob, "rationale": "v1 re-read"})


def _reread_v2_json(prob: float) -> str:
    return json.dumps(
        {
            "counter_strongest_point": "destocking may be structural",
            "rebuttal": "the filing calls it temporary and channel-specific",
            "probability_reversion_10d": prob,
            "rationale": "v2 re-derived",
        }
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


def _stubs(
    *,
    strong_rubric: float = 0.72,
    cross_rubric: float = 0.68,
    reread_v1: float = 0.66,
    reread_v2: float = 0.66,
) -> dict[ModelTier, StubLLMClient]:
    """One stub per tier. The STRONG stub serves the rubric (default) and both
    re-reads (matched by their distinct markers); the CROSS stub serves the
    rubric (default) and both counter-arguments. v2 markers are ordered first so
    the v1 substring never shadows them."""
    triage = StubLLMClient("haiku-stub", default=_TRIAGE_YES)
    strong = StubLLMClient(
        "opus-stub",
        [
            ("RE-DERIVE FROM SCRATCH", _reread_v2_json(reread_v2)),
            ("YOUR ORIGINAL ASSESSMENT", _reread_v1_json(reread_v1)),
        ],
        default=_rubric_json(prob=strong_rubric),
    )
    cross = StubLLMClient(
        "gpt-stub",
        [
            ("counter-pass v2", _COUNTER_V2_JSON),
            ("ARGUE THE CRASH IS CORRECT", _COUNTER_V1_JSON),
        ],
        default=_rubric_json(prob=cross_rubric),
    )
    return {ModelTier.TRIAGE: triage, ModelTier.STRONG: strong, ModelTier.CROSS_CHECK: cross}


def _router(clients: dict[ModelTier, StubLLMClient], cap_usd: float = 100.0) -> LLMRouter:
    return LLMRouter(clients, SpendTracker(cap_usd=cap_usd, label="test"))


# ---------------------------------------------------------------------------
# The substance of v2: the three prompt changes (assert on the real prompt)
# ---------------------------------------------------------------------------


class TestV2Prompt:
    def test_prompt_version_constant_is_frozen(self) -> None:
        assert PROMPT_VERSION_V2 == "fade-v2"
        # v2 shares v1's pre-registered threshold (no tuning).
        assert FADE_PROBABILITY_ENTRY_THRESHOLD == 0.55

    def test_v2_reread_omits_prior_probability_and_forces_engagement(self) -> None:
        clients = _stubs(strong_rubric=0.72, cross_rubric=0.68, reread_v2=0.64)
        router = _router(clients)
        result = assess_crash_event_versions(router, event=_event(), filing_text=_DOC)

        # The shared ensemble probability is the average of 0.72 and 0.68 = 0.70.
        v2 = result[PROMPT_VERSION_V2]
        assert v2.ensemble_probability == pytest.approx(0.70)

        # The v2 re-read prompt was sent to the STRONG tier; find it.
        reread_prompts = [
            p for p in clients[ModelTier.STRONG].prompts_seen if "RE-DERIVE FROM SCRATCH" in p
        ]
        assert len(reread_prompts) == 1
        reread = reread_prompts[0]
        # Change #1 — BLIND: the prior probability (0.70) is NOT in the prompt.
        assert "0.70" not in reread
        assert "probability_reversion_10d=" not in reread
        # Change #3 — re-derive, not "revise".
        assert "RE-DERIVE FROM SCRATCH" in reread
        assert "revise" not in reread.lower()

        # Change #2 — engagement before the number: the forced strongest-point
        # and rebuttal are parsed and recorded on the counter-pass result.
        assert v2.counter_pass["reread_strongest_point"] == "destocking may be structural"
        assert v2.counter_pass["reread_rebuttal"].startswith("the filing calls it temporary")
        assert v2.prompt_version == "fade-v2"

    def test_v1_reread_still_shows_the_prior_probability(self) -> None:
        # Guard: the v1 prompt is unchanged (still anchors on the prior number).
        clients = _stubs()
        router = _router(clients)
        assess_crash_event_versions(router, event=_event(), filing_text=_DOC)
        v1_reread = [
            p for p in clients[ModelTier.STRONG].prompts_seen if "YOUR ORIGINAL ASSESSMENT" in p
        ]
        assert len(v1_reread) == 1
        assert "probability_reversion_10d=0.70" in v1_reread[0]


# ---------------------------------------------------------------------------
# Shared ensemble — runs once, forks the counter-pass
# ---------------------------------------------------------------------------


class TestSharedEnsemble:
    def test_ensemble_runs_once_for_two_versions(self) -> None:
        clients = _stubs()
        router = _router(clients)
        result = assess_crash_event_versions(router, event=_event(), filing_text=_DOC)

        # Triage (the first ensemble call) happened exactly once for BOTH versions.
        assert len(clients[ModelTier.TRIAGE].prompts_seen) == 1
        # The rubric ran once per model: STRONG saw exactly one rubric prompt
        # (the rest are re-reads), CROSS saw exactly one rubric (rest are counters).
        strong_rubric = [
            p for p in clients[ModelTier.STRONG].prompts_seen
            if "DOCUMENT TEXT" in p and "COUNTER-ARGUMENT" not in p
        ]
        cross_rubric = [
            p for p in clients[ModelTier.CROSS_CHECK].prompts_seen
            if "ARGUE THE CRASH IS CORRECT" not in p
        ]
        assert len(strong_rubric) == 1
        assert len(cross_rubric) == 1
        # Both versions share the identical ensemble outputs.
        assert result[PROMPT_VERSION].ensemble_probability == pytest.approx(0.70)
        assert result[PROMPT_VERSION_V2].ensemble_probability == pytest.approx(0.70)
        assert (
            result[PROMPT_VERSION].model_probabilities
            == result[PROMPT_VERSION_V2].model_probabilities
        )

    def test_no_double_ensemble_spend(self) -> None:
        # One version: triage + strong + cross + (counter + reread) = 5 calls.
        single = _router(_stubs())
        assess_crash_event(single, event=_event(), filing_text=_DOC)
        assert single.tracker.calls == 5

        # Two versions: the SAME ensemble (3) + v1 counter-pass (2) + v2
        # counter-pass (2) = 7. Exactly +2 over a single version, proving the
        # ensemble was not re-run (a second ensemble would add 5, not 2).
        both = _router(_stubs())
        assess_crash_event_versions(both, event=_event(), filing_text=_DOC)
        assert both.tracker.calls == 7

    def test_non_candidate_runs_no_counter_pass_for_any_version(self) -> None:
        # Proportionate -> no candidate -> ensemble only, no counter calls.
        clients = _stubs()
        clients[ModelTier.STRONG]._default = _rubric_json(classification="proportionate", prob=0.70)
        clients[ModelTier.CROSS_CHECK]._default = _rubric_json(
            classification="proportionate", prob=0.70
        )
        router = _router(clients)
        result = assess_crash_event_versions(router, event=_event(), filing_text=_DOC)
        assert router.tracker.calls == 3  # triage + 2 rubric, no counter-pass
        for version in (PROMPT_VERSION, PROMPT_VERSION_V2):
            assert result[version].counter_pass == {}
            assert not result[version].passes_entry_threshold


# ---------------------------------------------------------------------------
# fade-v1 must be bit-identical whether or not v2 runs
# ---------------------------------------------------------------------------


def _assessment_fields(a: FadeAssessment) -> dict:
    return {
        "scored": a.scored,
        "classification": a.classification,
        "stated_probability": a.stated_probability,
        "ensemble_probability": a.ensemble_probability,
        "vetoed": a.vetoed,
        "flags": a.flags,
        "counter_pass": a.counter_pass,
        "model_probabilities": a.model_probabilities,
        "model_classifications": a.model_classifications,
        "rationale": a.rationale,
        "prompt_version": a.prompt_version,
    }


class TestV1BitIdentical:
    @pytest.mark.parametrize(
        "reread_v1,expected_veto",
        [(0.66, False), (0.40, True)],  # an entry and a veto
    )
    def test_v1_identical_with_or_without_v2(self, reread_v1: float, expected_veto: bool) -> None:
        solo = assess_crash_event(
            _router(_stubs(reread_v1=reread_v1)), event=_event(), filing_text=_DOC
        )
        shared_v1 = assess_crash_event_versions(
            _router(_stubs(reread_v1=reread_v1)), event=_event(), filing_text=_DOC
        )[PROMPT_VERSION]
        assert _assessment_fields(solo) == _assessment_fields(shared_v1)
        assert solo.vetoed is expected_veto
        # v1's counter-pass dict never grows the v2-only engagement fields.
        assert "reread_strongest_point" not in solo.counter_pass


# ---------------------------------------------------------------------------
# SharedFadeGate — per-event cache shared across version callables
# ---------------------------------------------------------------------------


class TestSharedFadeGate:
    def test_for_version_shares_one_ensemble_per_event(self, tmp_path: Path) -> None:
        router = _router(_stubs())
        gate = SharedFadeGate(router, CachedHTTP(cache_dir=tmp_path, offline=True), "t t@e.com")
        v1_gate = gate.for_version(PROMPT_VERSION)
        v2_gate = gate.for_version(PROMPT_VERSION_V2)
        ev = CrashEvent(
            event_id="NODOC-crash-2026-03-02", symbol="NODOC", as_of=D0,
            prev_close=50.0, close=42.0, drop_pct=-0.16,
        )
        a1 = v1_gate(ev)
        a2 = v2_gate(ev)
        # No source doc -> both unscored, distinct objects, version-tagged.
        assert not a1.scored and not a2.scored
        assert a1.prompt_version == "fade-v1" and a2.prompt_version == "fade-v2"
        assert "no_source_doc" in a1.flags
        # The second version call hit the cache (no extra spend, same dict).
        assert gate.assess(ev) is gate.assess(ev)

    def test_unknown_version_rejected(self, tmp_path: Path) -> None:
        gate = SharedFadeGate(
            _router(_stubs()), CachedHTTP(cache_dir=tmp_path, offline=True), "t t@e.com"
        )
        with pytest.raises(ValueError):
            gate.for_version("fade-v3")


# ---------------------------------------------------------------------------
# crash_fade_v2 lane — offline end-to-end through the strategy
# ---------------------------------------------------------------------------

START = datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
CRASH_DAY = 2


def _write_fade_dataset(data_dir: Path, prices: list[float]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "prices.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price", "bid", "ask", "volume"])
        w.writeheader()
        for day, price in enumerate(prices):
            w.writerow({
                "timestamp": (START + timedelta(days=day)).isoformat(),
                "symbol": "CRSH", "price": f"{price:.4f}", "bid": "", "ask": "",
                "volume": 200_000,
            })
    with (data_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(
            f, fieldnames=["event_id", "event_type", "symbol", "timestamp", "payload_json"]
        ).writeheader()


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


def _synthetic_detector():
    crash_date = (START + timedelta(days=CRASH_DAY)).date()

    def detect(now: datetime) -> list[CrashEvent]:
        if now.date() != crash_date:
            return []
        return [CrashEvent(
            event_id=f"CRSH-crash-{crash_date.isoformat()}",
            symbol="CRSH", as_of=now, prev_close=100.0, close=85.0,
            drop_pct=-0.15, market_cap=1e9, median_dollar_volume=2e6,
        )]

    return detect


def _v2_assessment(prob: float, vetoed: bool) -> FadeAssessment:
    return FadeAssessment(
        event_id=f"CRSH-crash-{(START + timedelta(days=CRASH_DAY)).date().isoformat()}",
        symbol="CRSH", scored=True, classification="overreaction",
        stated_probability=prob, ensemble_probability=0.70, vetoed=vetoed,
        counter_pass={
            "revised_probability": prob, "final_probability": prob, "vetoed": vetoed,
            "reread_strongest_point": "destocking may be structural",
            "reread_rebuttal": "the filing calls it temporary",
        },
        flags=(["counter_pass_veto"] if vetoed else []),
        prompt_version=PROMPT_VERSION_V2,
    )


_V2_LANE = RideConfig(ride_enabled=False, fade_enabled=True, fade_direction="fade_v2")


def _run_v2_lane(tmp_path: Path, prices: list[float], fade_gate) -> StateStore:
    data_dir = tmp_path / "data"
    _write_fade_dataset(data_dir, prices)
    db_path = tmp_path / "state.db"
    cfg = _fade_cfg(data_dir, db_path)

    def factory(_comp) -> Strategy:
        return ProportionalityRideStrategy(
            _V2_LANE, crash_detector=_synthetic_detector(), fade_gate=fade_gate,
        )

    drive_strategy(factory, cfg)
    return StateStore(db_path)


class TestV2Lane:
    def test_v2_entry_books_crash_fade_v2_bucket(self, tmp_path: Path) -> None:
        prices = [100.0, 100.0, 85.0] + [85.0] * 14
        store = _run_v2_lane(
            tmp_path, prices, fade_gate=lambda ev: _v2_assessment(0.66, vetoed=False)
        )
        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        assert [d["action"] for d in entries] == ["fade_buy"]
        assert entries[0]["inputs"]["strategy_bucket"] == "crash:fade_v2"
        # Hard rule 4: the counter-pass result (incl. v2's engagement fields) is
        # logged on the decision.
        cp = entries[0]["inputs"]["fade_gate"]["counter_pass"]
        assert cp["vetoed"] is False
        assert cp["reread_strongest_point"] == "destocking may be structural"

        resolutions = store.get_decisions(BOT_ID, "backtest", "resolution")
        assert resolutions[0]["inputs"]["strategy_bucket"] == "crash:fade_v2"
        with store.transaction() as conn:
            cal = conn.execute(
                "SELECT strategy_bucket FROM calibration_ledger"
            ).fetchall()
            theses = conn.execute("SELECT strategy_bucket FROM theses").fetchall()
        assert cal[0]["strategy_bucket"] == "crash:fade_v2"
        assert theses[0]["strategy_bucket"] == "crash:fade_v2"

    def test_v2_veto_blocks_entry_and_is_logged(self, tmp_path: Path) -> None:
        prices = [100.0, 100.0, 85.0, 85.0, 85.0]
        store = _run_v2_lane(
            tmp_path, prices, fade_gate=lambda ev: _v2_assessment(0.40, vetoed=True)
        )
        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        assert [d["action"] for d in entries] == ["fade_vetoed"]
        assert entries[0]["inputs"]["fade_gate"]["counter_pass"]["vetoed"] is True
        assert entries[0]["prompt_version"] == "fade-v2"
        assert store.get_fills(BOT_ID, "backtest") == []

    def test_v2_decisions_tagged_prompt_version(self, tmp_path: Path) -> None:
        prices = [100.0, 100.0, 85.0] + [85.0] * 14
        store = _run_v2_lane(
            tmp_path, prices, fade_gate=lambda ev: _v2_assessment(0.66, vetoed=False)
        )
        entries = store.get_decisions(BOT_ID, "backtest", "entry")
        assert entries[0]["prompt_version"] == "fade-v2"

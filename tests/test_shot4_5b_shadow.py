"""Ticket 5b — shadow mode (shot 4).

What must hold:
1. A shadow StrategyContext can NEVER be wired to a live broker — enforced at
   construction (the acceptance's "assert via test that the shadow path cannot
   reach a live broker").
2. A shadow lane logs every would-be order as a `shadow_order` decision row,
   with the payload a live broker would have received.
3. On the daily paper tick, the shadow lane runs as a third lane in the SAME
   process, makes the same entry decisions as the gated lane on the same
   events, and the gated/mech lanes' behaviour is unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockbot.bots.proportionality.crash import CrashEvent
from stockbot.bots.proportionality.fade import FadeAssessment
from stockbot.bots.proportionality.quality import QualityAssessment
from stockbot.bots.proportionality.ride import RideConfig
from stockbot.config import AppConfig, load_config
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import StrategyContext
from stockbot.data.live_feed import LiveDataFeed
from stockbot.data.real_loader import (
    ALPHAVANTAGE_EARNINGS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKERS_URL,
    TIINGO_DAILY_URL,
    CachedHTTP,
    cache_file_path,
)
from stockbot.harness.paper_run import (
    FADE_MECH_BOT_ID,
    GATED_BOT_ID,
    MECH_BOT_ID,
    SHADOW_BOT_ID,
    run_paper_tick,
)

UTC = timezone.utc
DAY1 = datetime(2026, 6, 10, 22, 0, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
CIK = 7654321


# ---------------------------------------------------------------------------
# 1. The unreachability guard
# ---------------------------------------------------------------------------


class _FakeLiveBroker:
    """Anything carrying is_live=True (the LiveBrokerRails marker)."""

    is_live = True


class _FakeSimBroker:
    is_live = False


def test_shadow_context_cannot_reach_a_live_broker() -> None:
    cfg = AppConfig()
    with pytest.raises(ValueError, match="shadow context cannot be wired to a live broker"):
        StrategyContext(
            config=cfg, datafeed=object(), broker=_FakeLiveBroker(),
            risk=object(), store=object(), shadow=True,
        )
    # Non-shadow contexts may hold live brokers; shadow + sim is fine.
    StrategyContext(config=cfg, datafeed=object(), broker=_FakeSimBroker(),
                    risk=object(), store=object(), shadow=True)


def test_real_live_brokers_carry_the_marker() -> None:
    from stockbot.core.broker.alpaca import AlpacaBroker
    from stockbot.core.broker.robinhood_mcp import RobinhoodMCPBroker
    from stockbot.core.broker.simulated import SimulatedBroker

    assert RobinhoodMCPBroker.is_live is True
    assert AlpacaBroker.is_live is True
    assert getattr(SimulatedBroker, "is_live", False) is False


def test_config_yaml_shadow_lane_is_registered_for_the_daily_tick() -> None:
    """The production daily tick (main -> config) includes the shadow lane."""
    cfg = load_config()
    assert cfg.shadow.enabled is True
    assert cfg.shadow.bot_id == SHADOW_BOT_ID


# ---------------------------------------------------------------------------
# 2 + 3. Shadow lane on the daily tick (offline LiveDataFeed fixtures,
#         same pattern as test_2d_paper_run)
# ---------------------------------------------------------------------------


def _seed_cache(cache_dir: Path, *, price: float, day: datetime) -> None:
    def put(url: str, payload, params=None) -> None:
        cache_file_path(cache_dir, url, params).write_text(json.dumps(payload), encoding="utf-8")

    put(SEC_TICKERS_URL, {"0": {"cik_str": CIK, "ticker": "PAPR", "title": "Paper Test Corp"}})
    put(
        SEC_SUBMISSIONS_URL.format(cik=CIK),
        {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "accessionNumber": ["0000000000-26-000777"],
                    "filingDate": ["2026-06-09"],
                    "acceptanceDateTime": ["2026-06-09T21:05:00.000Z"],
                    "items": ["2.02"],
                }
            }
        },
    )
    put(
        ALPHAVANTAGE_EARNINGS_URL,
        {
            "quarterlyEarnings": [
                {
                    "fiscalDateEnding": "2026-05-31",
                    "reportedDate": "2026-06-09",
                    "reportedEPS": "1.50",
                    "estimatedEPS": "1.00",
                }
            ]
        },
        params={"function": "EARNINGS", "symbol": "PAPR", "apikey": "av-key"},
    )
    put(
        TIINGO_DAILY_URL.format(ticker="PAPR"),
        [{"date": day.date().isoformat() + "T00:00:00.000Z", "adjClose": price, "adjVolume": 90000}],
        params={
            "startDate": (day.date() - timedelta(days=10)).isoformat(),
            "endDate": day.date().isoformat(),
            "resampleFreq": "daily",
            "token": "tiingo-key",
        },
    )


def _feed(tmp_path: Path, *, day: datetime, price: float) -> LiveDataFeed:
    cache_dir = tmp_path / f"cache-{day.date().isoformat()}"
    cache_dir.mkdir(exist_ok=True)
    _seed_cache(cache_dir, price=price, day=day)
    return LiveDataFeed(
        symbols=["PAPR"],
        state_path=tmp_path / "feed_state.json",
        tiingo_api_key="tiingo-key",
        alphavantage_api_key="av-key",
        sec_user_agent="test test@example.com",
        request_sleep_s=0.0,
        clock=lambda: day,
        client=CachedHTTP(cache_dir=cache_dir, offline=True),
    )


class _CountingGate:
    """Stub 2c gate WITH the per-event cache the real gate has, so the test
    proves the shadow lane re-uses assessments instead of re-spending."""

    def __init__(self, probability: float) -> None:
        self._p = probability
        self.scored_events: list[str] = []
        self._cache: dict[str, QualityAssessment] = {}

    @property
    def spend_summary(self) -> dict:
        return {"label": "stub", "spent_usd": 0.0, "calls": len(self.scored_events)}

    def __call__(self, ev) -> QualityAssessment:
        if ev.event_id in self._cache:
            return self._cache[ev.event_id]
        self.scored_events.append(ev.event_id)
        out = QualityAssessment(
            event_id=ev.event_id, symbol=ev.symbol, scored=True,
            quality_score=0.8, stated_probability=self._p,
            rationale="stub: durable beat",
        )
        self._cache[ev.event_id] = out
        return out


_RIDE = RideConfig(
    entry_mode="threshold",
    surprise_threshold=0.05,
    exit_rules_enabled=True,
    symbol_allowlist=frozenset({"PAPR"}),
)


def _tick(tmp_path: Path, *, day: datetime, price: float, gate, shadow: bool) -> dict:
    universe = tmp_path / "universe.csv"
    if not universe.exists():
        universe.write_text("symbol,sector\nPAPR,Technology\n", encoding="utf-8")
    return run_paper_tick(
        universe_path=universe,
        db_path=tmp_path / "paper.db",
        state_dir=tmp_path / "state",
        feed=_feed(tmp_path, day=day, price=price),
        quality_gate=gate,
        ride_config=_RIDE,
        shadow=shadow,
    )


def test_shadow_lane_runs_end_to_end_and_logs_shadow_orders(tmp_path) -> None:
    gate = _CountingGate(0.72)
    summary = _tick(tmp_path, day=DAY1, price=50.0, gate=gate, shadow=True)

    # Three lanes, one process, one feed cursor.
    assert set(summary["lanes"]) == {GATED_BOT_ID, MECH_BOT_ID, SHADOW_BOT_ID}
    # The shared gate cache means the event was scored ONCE, not twice.
    assert gate.scored_events == ["PAPR-earnings-0000000000-26-000777"]

    store = StateStore(tmp_path / "paper.db")
    # The shadow lane logged exactly what it would have sent live.
    shadow_orders = store.get_decisions(SHADOW_BOT_ID, "paper", "shadow_order")
    assert len(shadow_orders) == 1
    inputs = shadow_orders[0]["inputs"]
    assert inputs["symbol"] == "PAPR"
    assert inputs["side"] == "buy"
    assert inputs["mark_price"] == pytest.approx(50.0)
    assert inputs["thesis_id"]

    # Shadow entry decision matches the gated lane's on the same event.
    gated_entries = store.get_decisions(GATED_BOT_ID, "paper", "entry")
    shadow_entries = store.get_decisions(SHADOW_BOT_ID, "paper", "entry")
    assert [d["action"] for d in gated_entries] == ["buy"]
    assert [d["action"] for d in shadow_entries] == ["buy"]
    assert gated_entries[0]["inputs"]["event_id"] == shadow_entries[0]["inputs"]["event_id"]
    assert (
        gated_entries[0]["inputs"]["stated_probability"]
        == shadow_entries[0]["inputs"]["stated_probability"]
        == pytest.approx(0.72)
    )

    # The non-shadow lanes never write shadow_order rows.
    assert store.get_decisions(GATED_BOT_ID, "paper", "shadow_order") == []
    assert store.get_decisions(MECH_BOT_ID, "paper", "shadow_order") == []

    # Dashboard-visible: the lane has snapshots like any other.
    from stockbot.dashboard.data import list_bot_lanes

    lanes = {(lane.bot_id, lane.mode) for lane in list_bot_lanes(tmp_path / "paper.db")}
    assert (SHADOW_BOT_ID, "paper") in lanes


def test_shadow_lane_state_survives_daily_processes_and_existing_lanes_unchanged(tmp_path) -> None:
    _tick(tmp_path, day=DAY1, price=50.0, gate=_CountingGate(0.72), shadow=True)
    sidecar = json.loads(
        (tmp_path / "state" / "paper_strategy_state.json").read_text("utf-8")
    )
    assert set(sidecar) == {GATED_BOT_ID, MECH_BOT_ID, SHADOW_BOT_ID}
    assert len(sidecar[SHADOW_BOT_ID]["pending_orders"]) == 1

    # Day 2 (new process): the shadow entry settles like the others.
    _tick(tmp_path, day=DAY2, price=51.0, gate=_CountingGate(0.72), shadow=True)
    store = StateStore(tmp_path / "paper.db")
    for bot_id in (GATED_BOT_ID, MECH_BOT_ID, SHADOW_BOT_ID):
        fills = store.get_fills(bot_id, "paper")
        assert len(fills) == 1, f"{bot_id}: expected the restored entry to fill"
        assert fills[0]["thesis_id"]  # 2-R3 threading holds on every lane


# ---------------------------------------------------------------------------
# Shadow lane + fade (ride + fade mirror)
# ---------------------------------------------------------------------------


class _CountingFadeGate:
    """Stub fade gate WITH the per-event cache the real gate has."""

    def __init__(self, probability: float = 0.66) -> None:
        self._p = probability
        self.scored_events: list[str] = []
        self._cache: dict[str, FadeAssessment] = {}

    def __call__(self, ev) -> FadeAssessment:
        if ev.event_id in self._cache:
            return self._cache[ev.event_id]
        self.scored_events.append(ev.event_id)
        out = FadeAssessment(
            event_id=ev.event_id,
            symbol=ev.symbol,
            scored=True,
            classification="overreaction",
            stated_probability=self._p,
            ensemble_probability=0.70,
            counter_pass={
                "revised_probability": self._p,
                "final_probability": self._p,
                "vetoed": False,
            },
            rationale="stub: overreaction",
        )
        self._cache[ev.event_id] = out
        return out


def _seed_crash_cache(cache_dir: Path, *, price: float, day: datetime) -> None:
    def put(url: str, payload, params=None) -> None:
        cache_file_path(cache_dir, url, params).write_text(json.dumps(payload), encoding="utf-8")

    put(
        TIINGO_DAILY_URL.format(ticker="CRSH"),
        [{"date": day.date().isoformat() + "T00:00:00.000Z", "adjClose": price, "adjVolume": 90000}],
        params={
            "startDate": (day.date() - timedelta(days=10)).isoformat(),
            "endDate": day.date().isoformat(),
            "resampleFreq": "daily",
            "token": "tiingo-key",
        },
    )


def _crash_feed(tmp_path: Path, *, day: datetime, price: float) -> LiveDataFeed:
    cache_dir = tmp_path / f"crash-cache-{day.date().isoformat()}"
    cache_dir.mkdir(exist_ok=True)
    _seed_crash_cache(cache_dir, price=price, day=day)
    return LiveDataFeed(
        symbols=["CRSH"],
        state_path=tmp_path / "crash_feed_state.json",
        tiingo_api_key="tiingo-key",
        alphavantage_api_key="av-key",
        sec_user_agent="test test@example.com",
        request_sleep_s=0.0,
        clock=lambda: day,
        client=CachedHTTP(cache_dir=cache_dir, offline=True),
    )


def _synthetic_crash_detector(day: datetime):
    crash_date = day.date()

    def detect(now: datetime) -> list[CrashEvent]:
        if now.date() != crash_date:
            return []
        return [
            CrashEvent(
                event_id=f"CRSH-crash-{crash_date.isoformat()}",
                symbol="CRSH",
                as_of=now,
                prev_close=100.0,
                close=85.0,
                drop_pct=-0.15,
                market_cap=1e9,
                median_dollar_volume=2e6,
            )
        ]

    return detect


_FADE_RIDE = RideConfig(
    entry_mode="threshold",
    surprise_threshold=0.05,
    exit_rules_enabled=True,
    symbol_allowlist=frozenset({"CRSH"}),
    fade_enabled=True,
)


def _fade_tick(
    tmp_path: Path,
    *,
    day: datetime,
    price: float,
    fade_gate: _CountingFadeGate,
    shadow: bool,
) -> dict:
    universe = tmp_path / "crash_universe.csv"
    if not universe.exists():
        universe.write_text("symbol,sector\nCRSH,Technology\n", encoding="utf-8")
    return run_paper_tick(
        universe_path=universe,
        db_path=tmp_path / "crash_paper.db",
        state_dir=tmp_path / "crash_state",
        feed=_crash_feed(tmp_path, day=day, price=price),
        quality_gate=_CountingGate(0.72),
        ride_config=_FADE_RIDE,
        crash_detector=_synthetic_crash_detector(day),
        fade_gate=fade_gate,
        shadow=shadow,
        fade=True,
    )


def test_shadow_lane_fade_matches_gated_lane_with_shared_gate_cache(tmp_path) -> None:
    fade_gate = _CountingFadeGate(0.66)
    summary = _fade_tick(tmp_path, day=DAY1, price=85.0, fade_gate=fade_gate, shadow=True)

    expected_lanes = {GATED_BOT_ID, MECH_BOT_ID, SHADOW_BOT_ID, FADE_MECH_BOT_ID}
    assert set(summary["lanes"]) == expected_lanes
    # Shared fade-gate cache: one assessment for both gated + shadow lanes.
    assert fade_gate.scored_events == [f"CRSH-crash-{DAY1.date().isoformat()}"]

    store = StateStore(tmp_path / "crash_paper.db")
    gated_fades = [
        d for d in store.get_decisions(GATED_BOT_ID, "paper", "entry")
        if d["action"] == "fade_buy"
    ]
    shadow_fades = [
        d for d in store.get_decisions(SHADOW_BOT_ID, "paper", "entry")
        if d["action"] == "fade_buy"
    ]
    assert len(gated_fades) == 1
    assert len(shadow_fades) == 1
    assert gated_fades[0]["inputs"]["event_id"] == shadow_fades[0]["inputs"]["event_id"]
    assert gated_fades[0]["inputs"]["strategy_bucket"] == "crash:fade"
    assert shadow_fades[0]["inputs"]["strategy_bucket"] == "crash:fade"
    gated_fg = gated_fades[0]["inputs"]["fade_gate"]
    shadow_fg = shadow_fades[0]["inputs"]["fade_gate"]
    assert gated_fg["stated_probability"] == shadow_fg["stated_probability"] == pytest.approx(0.66)
    assert gated_fg["counter_pass"] == shadow_fg["counter_pass"]

    shadow_orders = store.get_decisions(SHADOW_BOT_ID, "paper", "shadow_order")
    fade_shadow_orders = [d for d in shadow_orders if d["inputs"]["symbol"] == "CRSH"]
    assert len(fade_shadow_orders) == 1
    assert fade_shadow_orders[0]["inputs"]["side"] == "buy"
    assert fade_shadow_orders[0]["inputs"]["mark_price"] == pytest.approx(85.0)


def test_shadow_fade_path_cannot_reach_live_broker() -> None:
    """Fade entries use the same shadow ctor guard as ride entries."""
    cfg = AppConfig()
    with pytest.raises(ValueError, match="shadow context cannot be wired to a live broker"):
        StrategyContext(
            config=cfg,
            datafeed=object(),
            broker=_FakeLiveBroker(),
            risk=object(),
            store=object(),
            shadow=True,
        )


def test_shadow_fade_without_shadow_flag_still_scores_fade_gate_once(tmp_path) -> None:
    """Gated-only tick with fade: baseline that shared gate isn't double-spent."""
    fade_gate = _CountingFadeGate(0.66)
    _fade_tick(tmp_path, day=DAY1, price=85.0, fade_gate=fade_gate, shadow=False)
    assert fade_gate.scored_events == [f"CRSH-crash-{DAY1.date().isoformat()}"]

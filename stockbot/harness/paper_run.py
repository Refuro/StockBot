"""Ticket 2d — daily forward-paper runner (the race begins).

One `drive_strategies` fan-out tick per trading day on the shared paper DB:
- `proportionality`       — 2b mechanical signal gated by 2c quality scores,
                            real stated probabilities, 2d exit rules ON.
                            With `fade.enabled` (2.5c) this lane also fades
                            graded overreactions: one engine, two trade types,
                            buckets `earnings:ride` + `crash:fade`.
- `proportionality_mech`  — the mechanical-only control twin: constant 0.55,
                            no LLM anywhere. The race between the two lanes is
                            the cleanest measure of whether the LLM layer adds
                            anything.
- `crash_fade_mech`       — (2.5c, config `fade.enabled`) fade-only control
                            twin: fades EVERY detected >=10% crash at constant
                            0.55, same exits — the rubric's value is measured
                            brain-vs-coin, mirroring the ride race.

Run (scheduled daily after the close, or manually):
    python -m stockbot.harness.paper_run --universe stockbot/data/universe_expanded.csv

State across daily one-tick processes:
- broker cash/positions      -> portfolio snapshots in the DB (resume=True)
- unsettled next-bar orders  -> exported/restored via the sidecar state file
- strategy open trades       -> exported/restored via the sidecar state file
- feed event cursor          -> the LiveDataFeed's own state file

Runtime LLM spend cap: $5/day, enforced in code by the router's SpendTracker;
spend is logged per run. If the cap trips mid-run the tick fails loudly —
never improvised around (hard rule 6).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from stockbot.bots.frontier.propagation import (
    FrontierConfig,
    FrontierPropagationStrategy,
    LiveAnchorShockDetector,
    LiveAnchorUpShockDetector,
    LivePriceSnapshot,
)
from stockbot.bots.proportionality.crash import LiveCrashDetector, load_band_symbols
from stockbot.bots.proportionality.fade import (
    PROMPT_VERSION,
    PROMPT_VERSION_V2,
    CrashFadeGate,
    SharedFadeGate,
)
from stockbot.bots.proportionality.quality import EarningsQualityGate
from stockbot.bots.proportionality.ride import ProportionalityRideStrategy, RideConfig
from stockbot.research.frontier_backtest import (
    distinct_anchors,
    load_sector_map,
    load_tradeable_edges,
)
from stockbot.config import load_config, load_env
from stockbot.core.broker.factory import create_broker
from stockbot.core.broker.simulated import SimulatedBroker
from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.llm import build_router
from stockbot.core.risk.engine import RiskEngine
from stockbot.core.state.store import StateStore
from stockbot.core.strategy import Strategy
from stockbot.data.live_feed import LiveDataFeed
from stockbot.data.real_loader import CachedHTTP, load_sec_ticker_map
from stockbot.data.universe import load_universe
from stockbot.harness.loop import drive_strategies
from stockbot.harness.runtime import SpineComponents
from stockbot.harness.trading_calendar import is_us_trading_day
from stockbot.types import ExecutionMode

logger = logging.getLogger(__name__)

GATED_BOT_ID = "proportionality"
MECH_BOT_ID = "proportionality_mech"
SHADOW_BOT_ID = "proportionality_shadow"  # 5b: shadow lane (config: shadow.enabled)
FADE_MECH_BOT_ID = "crash_fade_mech"      # 2.5c: fade control twin (config: fade.enabled)
FADE_V2_BOT_ID = "crash_fade_v2"          # shot 11: fade-v2 lane (config: fade.v2_enabled)
FRONTIER_BOT_ID = "frontier"              # 3d: graph-driven propagation lane
FRONTIER_MECH_BOT_ID = "frontier_mech"    # 3d: propagation control twin (graph-vs-coin)
DAILY_LLM_CAP_USD = 5.0


def _paper_ride_config(allowlist: frozenset[str]) -> RideConfig:
    """2d paper parameters: exits ON, expanded-universe allowlist."""
    return RideConfig(
        entry_mode="top_decile",
        exit_rules_enabled=True,
        symbol_allowlist=allowlist,
    )


def seed_surprise_history(events_csv: str | Path) -> list[float]:
    """Chronological standardized surprises from a historical events CSV.

    Used to pre-load the expanding-decile history on the FIRST paper run, so
    the pre-registered top-decile rule starts informed instead of waiting weeks
    for 40 live surprises. Point-in-time safe: every seeded event is in the past.
    """
    import csv as _csv

    from stockbot.bots.proportionality.watcher import extract_earnings_surprises
    from stockbot.types import MarketEvent

    events: list[MarketEvent] = []
    with Path(events_csv).open(encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            events.append(
                MarketEvent(
                    event_id=row["event_id"],
                    event_type=row["event_type"],
                    symbol=row["symbol"],
                    as_of=datetime.fromisoformat(row["timestamp"]),
                    payload=json.loads(row["payload_json"]),
                )
            )
    events.sort(key=lambda e: e.as_of)
    return [s.surprise_pct for s in extract_earnings_surprises(events)]


def run_paper_tick(
    *,
    universe_path: str | Path,
    db_path: str | Path = "data/paper.db",
    state_dir: str | Path = "data",
    cache_root: str | Path = "data/live_cache",
    llm_cap_usd: float = DAILY_LLM_CAP_USD,
    seed_events_csv: str | Path | None = None,
    feed: LiveDataFeed | None = None,
    quality_gate=None,
    ride_config: RideConfig | None = None,
    shadow: bool = False,
    fade: bool = False,
    fade_gate=None,
    fade_v2: bool = False,
    fade_v2_gate=None,
    crash_detector=None,
    universe_metadata: str | Path = "stockbot/data/universe_expanded.metadata.json",
    frontier: bool = False,
    frontier_up: bool = True,
    frontier_graph_db: str | Path = "data/state.db",
    anchor_shock_detector=None,
    anchor_up_shock_detector=None,
    frontier_price_snapshot=None,
) -> dict:
    """Run exactly one fan-out paper tick; returns a summary dict.

    `feed` / `quality_gate` / `ride_config` / `fade_gate` / `crash_detector`
    are injectable for tests; production builds real ones.

    `shadow` (5b) adds a third lane — same brain and quality/fade gates as the
    gated lane (the gates' per-event caches mean zero extra LLM spend), but its
    context logs every order as a `shadow_order` decision row and can never
    reach a live broker. With `fade` ON the shadow lane mirrors both trade
    types (ride + fade) using the same detector/gate instances. ONE process,
    same feed cursor — the gated/mech lanes' semantics are untouched.
    Production enables it via config `shadow.enabled` (read in `main`), so
    direct callers keep the original two-lane behaviour.

    `fade` (2.5c) grows the GATED lane's strategy with fade entries (one
    engine, two trade types) and adds the `crash_fade_mech` control twin —
    fade-only, every detected crash, constant 0.55, same exits. The detector
    instance is shared across lanes (per-day cache: one Tiingo IEX sweep) and
    the fade gate shares the quality gate's router, so the whole tick stays
    inside the single $5/day cap. Default OFF: direct callers keep the
    existing lane set bit-identically.
    """
    env = load_env()
    cfg = load_config()
    cfg.mode = ExecutionMode.PAPER
    cfg.state.db_path = str(db_path)

    symbols = [e.symbol for e in load_universe(universe_path)]
    allowlist = frozenset(symbols)

    if feed is None:
        feed = LiveDataFeed(
            symbols=symbols,
            cache_root=cache_root,
            state_path=Path(state_dir) / "paper_feed_state.json",
            tiingo_api_key=env.tiingo_api_key,
            alphavantage_api_key=env.alphavantage_api_key,
            sec_user_agent=env.sec_user_agent,
        )

    store = StateStore(cfg.state.db_path)
    comp = SpineComponents(
        config=cfg,
        datafeed=feed,
        broker=create_broker(cfg),
        risk=RiskEngine(cfg.risk),
        store=store,
        calibration=CalibrationLedger(store),
    )

    # Shot 11: the v2 lane shares the gated lane's per-crash rubric ensemble, so
    # it needs the v1 fade side present to share with. Turning v2 on implies fade.
    if fade_v2:
        fade = True

    # ONE router (= one SpendTracker) for everything LLM in this tick, so the
    # $5/day cap is global across the quality gate AND the fade gate(s).
    router = None
    gate_client = None
    need_fade_router = fade and (fade_gate is None or (fade_v2 and fade_v2_gate is None))
    if quality_gate is None or need_fade_router:
        router = build_router(cap_usd=llm_cap_usd, label="paper-2c", env=env)
        gate_client = CachedHTTP(cache_dir=Path(cache_root) / "filings", offline=False)
    if quality_gate is None:
        quality_gate = EarningsQualityGate(router, gate_client, env.sec_user_agent)
    if fade and need_fade_router:
        if fade_v2:
            # Shot 11: one gate runs the rubric ensemble ONCE per crash and
            # forks the counter-pass per version — the gated lane reads fade-v1,
            # the crash_fade_v2 lane reads fade-v2, no double ensemble spend.
            shared_fade_gate = SharedFadeGate(router, gate_client, env.sec_user_agent)
            if fade_gate is None:
                fade_gate = shared_fade_gate.for_version(PROMPT_VERSION)
            if fade_v2_gate is None:
                fade_v2_gate = shared_fade_gate.for_version(PROMPT_VERSION_V2)
        elif fade_gate is None:
            fade_gate = CrashFadeGate(router, gate_client, env.sec_user_agent)
    if fade and crash_detector is None:
        band = load_band_symbols(universe_metadata)
        day_client = CachedHTTP(
            cache_dir=Path(cache_root) / datetime.now(timezone.utc).date().isoformat(),
            offline=False,
        )
        detector = LiveCrashDetector(
            band=band,
            client=day_client,
            tiingo_api_key=env.tiingo_api_key,
            sec_user_agent=env.sec_user_agent,
            ticker_map=load_sec_ticker_map(day_client, env.sec_user_agent),
        )
        crash_detector = detector.detect

    # Frontier (3d): the pre-verified graph edge set + anchor detector + the
    # sector membership for sector-neutral resolution. Both frontier lanes share
    # one anchor detector instance (per-day cache: one IEX sweep of the anchors)
    # and one price-snapshot cache — the graph lane and the mech twin see the
    # identical shock list and prices. ALL additive; the ride/fade lanes are
    # untouched. No LLM (mechanical propagation), so no router involvement.
    frontier_edges: list[dict] = []
    frontier_sector_members: dict[str, list[str]] = {}
    if frontier:
        frontier_edges = load_tradeable_edges(frontier_graph_db)
        _, frontier_sector_members = load_sector_map(universe_metadata)
        if anchor_shock_detector is None or frontier_price_snapshot is None:
            day_client = CachedHTTP(
                cache_dir=Path(cache_root) / datetime.now(timezone.utc).date().isoformat(),
                offline=False,
            )
            if anchor_shock_detector is None:
                anchor_shock_detector = LiveAnchorShockDetector(
                    anchors=distinct_anchors(frontier_edges),
                    client=day_client,
                    tiingo_api_key=env.tiingo_api_key,
                ).detect
            if frontier_price_snapshot is None:
                frontier_price_snapshot = LivePriceSnapshot(
                    client=day_client, tiingo_api_key=env.tiingo_api_key
                )
            # 9e: the additive UP-shock detector (positive anchor moves). Same
            # anchor set + per-day cache. Only built when the up side is on and
            # not injected; absent -> the down-only behaviour is unchanged.
            if frontier_up and anchor_up_shock_detector is None:
                anchor_up_shock_detector = LiveAnchorUpShockDetector(
                    anchors=distinct_anchors(frontier_edges),
                    client=day_client,
                    tiingo_api_key=env.tiingo_api_key,
                ).detect

    sidecar_path = Path(state_dir) / "paper_strategy_state.json"
    sidecar: dict = {}
    if sidecar_path.exists():
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))

    ride_cfg = ride_config or _paper_ride_config(allowlist)
    strategies: dict[str, Strategy] = {}
    brokers: dict[str, SimulatedBroker] = {}

    seeded_history: list[float] = []
    if seed_events_csv is not None and Path(seed_events_csv).exists():
        seeded_history = seed_surprise_history(seed_events_csv)

    def _make(bot_id: str, gate, cfg: RideConfig | None = None,
              detector=None, fgate=None) -> Strategy:
        strat = ProportionalityRideStrategy(
            cfg or ride_cfg, bot_id=bot_id, quality_gate=gate,
            crash_detector=detector, fade_gate=fgate,
        )
        saved = sidecar.get(bot_id)
        if saved:
            strat.restore_state(saved.get("strategy", {}))
        elif seeded_history:
            # First run only: pre-load the expanding-decile history.
            strat.restore_state({"surprise_history": seeded_history, "open": [], "closing": []})
        strategies[bot_id] = strat
        return strat

    def gated_factory(_comp) -> Strategy:
        # 2.5c: with fade ON the gated lane runs both trade types — the
        # ride side's semantics are untouched (fade entries are additive).
        if fade:
            cfg = dataclasses.replace(ride_cfg, fade_enabled=True)
            return _make(GATED_BOT_ID, quality_gate, cfg, crash_detector, fade_gate)
        return _make(GATED_BOT_ID, quality_gate)

    def mech_factory(_comp) -> Strategy:
        return _make(MECH_BOT_ID, None)

    def fade_mech_factory(_comp) -> Strategy:
        # Fade-only control twin: no ride side, no gate — every crash, 0.55.
        cfg = dataclasses.replace(ride_cfg, ride_enabled=False, fade_enabled=True)
        return _make(FADE_MECH_BOT_ID, None, cfg, crash_detector, None)

    def fade_v2_factory(_comp) -> Strategy:
        # Shot 11: fade-only v2 lane — same crashes, exits, and sizing as v1,
        # but it enters on the v2 counter-pass's veto decision and books the
        # crash:fade_v2 bucket (prompt_version fade-v2). Its gate shares the
        # gated lane's per-crash ensemble cache (only ~2 marginal calls).
        cfg = dataclasses.replace(
            ride_cfg, ride_enabled=False, fade_enabled=True, fade_direction="fade_v2"
        )
        return _make(FADE_V2_BOT_ID, None, cfg, crash_detector, fade_v2_gate)

    def _make_frontier(bot_id: str, apply_cap: bool) -> Strategy:
        # 9e: up side activates only when enabled AND an up detector is present
        # (so callers that inject only the down detector stay bit-identical).
        up_on = frontier_up and anchor_up_shock_detector is not None
        strat = FrontierPropagationStrategy(
            FrontierConfig(apply_concurrency_cap=apply_cap, up_enabled=up_on),
            bot_id=bot_id,
            anchor_shock_detector=anchor_shock_detector,
            up_anchor_shock_detector=anchor_up_shock_detector,
            sector_members=frontier_sector_members,
            price_snapshot=frontier_price_snapshot,
            edges=frontier_edges,
        )
        saved = sidecar.get(bot_id)
        if saved:
            strat.restore_state(saved.get("strategy", {}))
        strategies[bot_id] = strat
        return strat

    def frontier_factory(_comp) -> Strategy:
        # Graph lane: graph-driven, applies the max-concurrent selection cap.
        return _make_frontier(FRONTIER_BOT_ID, apply_cap=True)

    def frontier_mech_factory(_comp) -> Strategy:
        # Control twin: every observable PIT-valid linked name, no cap (coin).
        return _make_frontier(FRONTIER_MECH_BOT_ID, apply_cap=False)

    def shadow_factory(_comp) -> Strategy:
        # Same gate INSTANCES as the gated lane: the per-event caches guarantee
        # the shadow lane re-uses assessments instead of re-spending (ride +
        # fade when fade is ON).
        shadow_cfg = ride_cfg
        detector = None
        fgate = None
        if fade:
            shadow_cfg = dataclasses.replace(ride_cfg, fade_enabled=True)
            detector = crash_detector
            fgate = fade_gate
        strat = ProportionalityRideStrategy(
            shadow_cfg,
            bot_id=SHADOW_BOT_ID,
            quality_gate=quality_gate,
            crash_detector=detector,
            fade_gate=fgate,
        )
        saved = sidecar.get(SHADOW_BOT_ID)
        if saved:
            strat.restore_state(saved.get("strategy", {}))
        else:
            # First shadow run joins a race in progress: inherit the GATED
            # lane's surprise history so the expanding-decile entry rule makes
            # the same calls on the same events (the shadow week's comparison).
            # Open/closing trades are NOT inherited — the shadow book starts flat.
            gated_history = (
                sidecar.get(GATED_BOT_ID, {}).get("strategy", {}).get("surprise_history")
                or seeded_history
            )
            strat.restore_state(
                {"surprise_history": list(gated_history), "open": [], "closing": []}
            )
        strategies[SHADOW_BOT_ID] = strat
        return strat

    factories = [gated_factory, mech_factory]
    lane_bot_ids = [GATED_BOT_ID, MECH_BOT_ID]
    if shadow:
        factories.append(shadow_factory)
        lane_bot_ids.append(SHADOW_BOT_ID)
    if fade:
        factories.append(fade_mech_factory)
        lane_bot_ids.append(FADE_MECH_BOT_ID)
    if fade_v2:
        factories.append(fade_v2_factory)
        lane_bot_ids.append(FADE_V2_BOT_ID)
    if frontier:
        factories.extend([frontier_factory, frontier_mech_factory])
        lane_bot_ids.extend([FRONTIER_BOT_ID, FRONTIER_MECH_BOT_ID])

    # Pre-build one broker per lane and restore its unsettled next-bar orders
    # so yesterday's entries fill at today's prices (true next-bar in paper).
    lane_brokers = []
    for bot_id in lane_bot_ids:
        broker = create_broker(cfg)
        if isinstance(broker, SimulatedBroker):
            pending = sidecar.get(bot_id, {}).get("pending_orders", [])
            if pending:
                broker.restore_pending_orders(pending)
        brokers[bot_id] = broker
        lane_brokers.append(broker)
    comp.broker = lane_brokers[0]

    results = drive_strategies(
        factories,
        cfg,
        resume=True,  # broker cash/positions from the last snapshot
        components=comp,
        lane_brokers=lane_brokers,
        shadow_bot_ids=frozenset({SHADOW_BOT_ID}) if shadow else frozenset(),
    )

    # Persist the sidecar for tomorrow's process.
    new_sidecar: dict = {}
    for bot_id, strat in strategies.items():
        entry: dict = {"strategy": strat.export_state()}
        broker = brokers.get(bot_id)
        if isinstance(broker, SimulatedBroker):
            entry["pending_orders"] = broker.export_pending_orders()
        new_sidecar[bot_id] = entry
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(new_sidecar, indent=2), encoding="utf-8")

    spend = quality_gate.spend_summary if hasattr(quality_gate, "spend_summary") else {}
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(symbols),
        "lanes": {
            bot_id: {
                "run_id": r.run_id,
                "fills": r.fills,
                "snapshots": r.snapshots,
                "end_as_of": r.end_as_of,
            }
            for bot_id, r in results.items()
        },
        "llm_spend": spend,
    }
    logger.info("paper tick complete: %s", json.dumps(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily forward-paper tick (2d)")
    parser.add_argument("--universe", default="stockbot/data/universe_expanded.csv")
    parser.add_argument("--db", default="data/paper.db")
    parser.add_argument("--state-dir", default="data")
    parser.add_argument("--cache-root", default="data/live_cache")
    parser.add_argument("--llm-cap", type=float, default=DAILY_LLM_CAP_USD)
    parser.add_argument(
        "--seed-events",
        default="data/real_expanded/events.csv",
        help="Historical events CSV used to pre-load the decile history on FIRST run",
    )
    parser.add_argument(
        "--shadow",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add the 5b shadow lane (default: config shadow.enabled)",
    )
    parser.add_argument(
        "--fade",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add the 2.5c fade side + crash_fade_mech lane (default: config fade.enabled)",
    )
    parser.add_argument(
        "--fade-v2",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add the shot-11 crash_fade_v2 lane (default: config fade.v2_enabled)",
    )
    parser.add_argument(
        "--frontier",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add the 3d frontier + frontier_mech lanes (default: config frontier.enabled)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Tick even on a non-trading day (demos/diagnostics only)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 5-R1: on a non-trading day the feed still serves the LAST session's
    # prices, so ticking would settle Friday's next-bar orders at Friday's own
    # price and re-detect Friday's crashes under fresh event_ids. Don't tick.
    today = datetime.now(timezone.utc).date()
    if not args.force and not is_us_trading_day(today):
        summary = {"skipped": "non_trading_day", "date": today.isoformat()}
        logger.info("paper tick skipped: %s", json.dumps(summary))
        print(json.dumps(summary, indent=2))
        return 0
    cfg = load_config()
    shadow = args.shadow if args.shadow is not None else cfg.shadow.enabled
    fade = args.fade if args.fade is not None else cfg.fade.enabled
    fade_v2 = args.fade_v2 if args.fade_v2 is not None else cfg.fade.v2_enabled
    frontier = args.frontier if args.frontier is not None else cfg.frontier.enabled
    summary = run_paper_tick(
        universe_path=args.universe,
        db_path=args.db,
        state_dir=args.state_dir,
        cache_root=args.cache_root,
        llm_cap_usd=args.llm_cap,
        seed_events_csv=args.seed_events,
        shadow=shadow,
        fade=fade,
        fade_v2=fade_v2,
        universe_metadata=cfg.fade.universe_metadata,
        frontier=frontier,
        frontier_graph_db=cfg.frontier.graph_db,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

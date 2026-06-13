"""Frontier Engine trading code (ticket 3c/3d) — price-shock PROPAGATION.

When a price shock hits a company you can *observe* (a counterparty with a
resolved ticker), trade the mechanically-linked small-cap names the
pre-verified graph says depend on it. This is the second engine, a sibling of
the proportionality ride/fade sides — NOT a fork: it reuses the same
`Strategy`/`StrategyContext` plumbing, the same calibration ledger, the same
additive/config-gated lane pattern.

THE CORE SEMANTICS ARE SHARED (the "why one agent" rule). The live strategy and
the historical sign-accuracy backtest both import the *same*:
  - per-edge-type direction map (`DIRECTION_MAP`),
  - PIT "edge live as-of date" selection (`pit_valid_tradeable_edges`),
  - sector-neutralization (`sector_benchmark_peers` + `sign_is_correct`).
A divergence there would mean we *measure* one thing and *trade* another. Don't.

PRE-REGISTRATION (hard rule 3 — frozen in code BEFORE any backtest is scored):
every constant in the "pre-registered" block below is the falsifiable
hypothesis. If you tune them after seeing results, STOP and write it down.

The hard constraint that shapes the engine (don't fight the data): a
price-shock can only propagate from a counterparty you can *observe*, i.e.
``counterparty_symbol IS NOT NULL``. Only that subset of the named edges is
tradeable by price propagation, and it is small.

`regulatory_precedent` is OUT of scope for price-anchor propagation — you cannot
price-shock-anchor on "the EPA". It is measure-only / deferred (see
`reports/shot8-report.md`); this module never trades it.

Long-only disposition: both tradeable edge types predict the filer DOWN
(a negative shock to a key customer/supplier threatens the filer). The live
broker stack is long-only (Robinhood beta), so this engine cannot place a real
short. It is therefore a *prediction-and-resolution* engine: it records a
directional thesis per signal and resolves it on the filer's realized
sector-neutral excess return over the holding window — exactly the quantity the
≥55% per-bucket sign-accuracy gate is judged on. No BUY orders are placed (so
RiskEngine/PIT are honored trivially; nothing is weakened).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.strategy import Strategy, StrategyContext
from stockbot.types import OrderRequest, OrderStatus, OrderType, Side

logger = logging.getLogger(__name__)

# =============================================================================
# PRE-REGISTERED CONSTANTS (frozen BEFORE the backtest was scored — hard rule 3)
# =============================================================================

BOT_ID = "frontier"
MECH_BOT_ID = "frontier_mech"

# Anchor-shock definition: a close-to-close move on the OBSERVABLE counterparty
# (the anchor). DIRECTION POLICY = down-only — the falsifiable hypothesis is
# about NEGATIVE shocks propagating (a customer/supplier in trouble threatens
# the filer). A bidirectional policy would conflate good news, a different
# mechanism. Threshold pre-registered at 5%: anchors are large caps, where a
# single-session 5% drop is already a real, news-driven shock (10% would be
# near-never for mega-caps; F-0 reports the frequency at the chosen threshold).
ANCHOR_SHOCK_THRESHOLD = 0.05
ANCHOR_SHOCK_DIRECTION = "down"  # down-only

# The two price-propagation-tradeable edge types and the falsifiable direction
# each implies on the FILER (regulatory_precedent is OUT — measure-only).
TRADEABLE_EDGE_TYPES: tuple[str, ...] = ("customer_concentration", "sole_supplier")
DIRECTION_MAP: dict[str, str] = {
    # anchor = the filer's big CUSTOMER: a negative shock to the customer
    # threatens the filer's demand -> predict filer DOWN.
    "customer_concentration": "down",
    # anchor = the filer's sole SUPPLIER: a negative shock to the supplier
    # threatens the filer's supply -> predict filer DOWN.
    "sole_supplier": "down",
    # regulatory_precedent: intentionally absent — not a price anchor.
}

# PIT liveness: an edge is live only while filed_at <= as_of <= filed_at + 18mo
# (matches StateStore.get_live_frontier_edges default of 548 days).
EDGE_MAX_AGE_DAYS = 548

# Holding window: enter T+1 (the first trading day AFTER the shock), hold N
# trading days, resolve on the sector-neutral excess return over the window.
ENTRY_OFFSET_TRADING_DAYS = 1
HOLD_TRADING_DAYS = 5

# Sizing (mirrors the fade side): small, <=3% equity, max-concurrent cap. The
# cap is the graph lane's only mechanical SELECTION — it binds when one anchor
# links several filers (e.g. a WMT shock links 6 names), which is exactly where
# the graph lane and the no-selection mech twin diverge.
POSITION_PCT = 0.03
MAX_CONCURRENT = 2

# Protective stop, pre-registered for parity with ride/fade. For a prediction-
# only measurement lane (no capital position) the holding-window time-stop is
# the resolution trigger; the hard stop is frozen here but cannot trigger an
# early exit on a position that does not exist (documented in the report).
HARD_STOP_PCT = 0.08

# Mechanical constant probability — there is NO LLM in this engine (hard rule
# 5). It calibrates the bucket plumbing; the gate is the per-bucket
# sign-accuracy (hit rate), not the Brier of this constant.
STATED_PROBABILITY = 0.55

# Sector neutralization: filer excess = filer_return - sector_benchmark_return,
# where the benchmark is the EQUAL-WEIGHT mean return of up to N peers in the
# filer's sector (deterministic, sorted by symbol, the filer excluded). The cap
# bounds live cost and is applied IDENTICALLY in the backtest (shared function).
SECTOR_BENCHMARK_MAX_PEERS = 40


def trade_type_for(edge_type: str) -> str:
    return f"frontier_{edge_type}"


def strategy_bucket_for(edge_type: str) -> str:
    """Per-edge-type capital bucket: "frontier:customer_concentration" etc.

    Buckets advance or die independently (Phase 3 rule).
    """
    return CalibrationLedger.strategy_bucket(BOT_ID, edge_type)


def predicted_direction(edge_type: str) -> str | None:
    """The falsifiable direction the edge type implies on the filer, or None."""
    return DIRECTION_MAP.get(edge_type)


# =============================================================================
# PRE-REGISTERED UP-SIDE CONSTANTS (ticket 9e — frozen BEFORE any up scoring)
# =============================================================================
# Strictly ADDITIVE & ISOLATED from the down-only block above (shot 8 froze that
# block and its measurement; this does NOT edit it). The cheap, long-expressible
# test: does a POSITIVE anchor shock propagate — a big customer/supplier has a
# great day -> the dependent filer rises? If it does, a long-only cash account
# can trade it with a normal BUY (no shorting, no margin, no options).
#
# Mirrors the down side's 5% magnitude, direction UP. SEPARATE buckets and
# trade-types (`frontier:customer_concentration_up`) so up and down are scored
# independently and NEVER commingled.
ANCHOR_UP_SHOCK_THRESHOLD = 0.05  # mirror the down-only magnitude, direction UP
ANCHOR_UP_SHOCK_DIRECTION = "up"

UP_DIRECTION_MAP: dict[str, str] = {
    # anchor = the filer's big CUSTOMER having a great day -> demand tailwind ->
    # predict filer UP.
    "customer_concentration": "up",
    # anchor = the filer's sole SUPPLIER strong -> supply/relationship tailwind
    # -> predict filer UP.
    "sole_supplier": "up",
    # regulatory_precedent: intentionally absent — not a price anchor (same as
    # the down side).
}


def is_anchor_up_shock(prev_close: float, close: float) -> bool:
    """Pre-registered UP-only shock test on an anchor's close-to-close move."""
    if prev_close <= 0 or close <= 0:
        return False
    move = (close - prev_close) / prev_close
    return move >= ANCHOR_UP_SHOCK_THRESHOLD


def up_predicted_direction(edge_type: str) -> str | None:
    """The UP-side falsifiable direction the edge type implies, or None."""
    return UP_DIRECTION_MAP.get(edge_type)


def up_trade_type_for(edge_type: str) -> str:
    return f"frontier_{edge_type}_up"


def up_strategy_bucket_for(edge_type: str) -> str:
    """Per-edge-type UP capital bucket: "frontier:customer_concentration_up".

    Separate from the down bucket so up and down advance or die independently
    and are never commingled (hard rule for 9e).
    """
    return CalibrationLedger.strategy_bucket(BOT_ID, f"{edge_type}_up")


# =============================================================================
# Shared anchor-shock + PIT + sector-neutral semantics
# =============================================================================


@dataclass(frozen=True)
class AnchorShock:
    """A detected close-to-close shock on an observable counterparty (anchor)."""

    anchor_symbol: str
    as_of: datetime
    prev_close: float
    close: float
    move_pct: float  # negative fraction for a down shock, e.g. -0.064


def is_anchor_shock(prev_close: float, close: float) -> bool:
    """Pre-registered down-only shock test on an anchor's close-to-close move."""
    if prev_close <= 0 or close <= 0:
        return False
    move = (close - prev_close) / prev_close
    return move <= -ANCHOR_SHOCK_THRESHOLD


def pit_valid_tradeable_edges(
    edges: list[dict[str, Any]], as_of: datetime
) -> list[dict[str, Any]]:
    """Edges live as-of `as_of`, observable (resolved ticker), tradeable type.

    PIT is law (hard rule 4): only edges with
    ``filed_at <= as_of <= filed_at + 18 months``. Never sees an edge before
    its filing date. Anonymous / unresolved counterparties are dropped — a
    shock can only propagate from a counterparty you can observe.
    """
    as_of_date = as_of.date()
    cutoff = (as_of - timedelta(days=EDGE_MAX_AGE_DAYS)).date()
    out: list[dict[str, Any]] = []
    for e in edges:
        if e.get("edge_type") not in DIRECTION_MAP:
            continue
        if not e.get("counterparty_symbol"):
            continue
        filed_raw = str(e.get("filed_at", ""))[:10]
        try:
            filed = datetime.fromisoformat(filed_raw).date()
        except ValueError:
            continue
        if filed > as_of_date or filed < cutoff:
            continue
        out.append(e)
    return out


def sector_benchmark_peers(
    filer: str,
    sector_members: list[str],
    *,
    max_peers: int = SECTOR_BENCHMARK_MAX_PEERS,
) -> list[str]:
    """Deterministic equal-weight peer set for the filer's sector benchmark.

    Sorted by symbol, the filer itself excluded, capped to `max_peers`. The
    backtest and the live lane MUST call this identically (shared semantics).
    """
    peers = sorted({s.upper() for s in sector_members if s.upper() != filer.upper()})
    return peers[:max_peers]


def sector_neutral_excess(
    filer_return: float, peer_returns: list[float]
) -> float | None:
    """filer_return - equal-weight mean peer return. None if no peers."""
    if not peer_returns:
        return None
    benchmark = sum(peer_returns) / len(peer_returns)
    return filer_return - benchmark


def sign_is_correct(direction: str, excess_return: float) -> bool:
    """Did the sector-neutral excess return match the predicted direction?

    "down" prediction is correct iff the filer UNDERperformed its sector
    (excess < 0); "up" iff it outperformed.
    """
    if direction == "down":
        return excess_return < 0
    return excess_return > 0


# =============================================================================
# FrontierPropagationStrategy
# =============================================================================

# Injected anchor-shock detector: as-of timestamp -> today's anchor shocks.
AnchorShockDetector = Callable[[datetime], list[AnchorShock]]
# Optional batched price snapshot: symbols -> {symbol: price} (only available
# ones). When None the strategy falls back to per-symbol datafeed quotes.
PriceSnapshot = Callable[[list[str]], dict[str, float]]


@dataclass
class FrontierConfig:
    """Pre-registered mechanical parameters (do not tune after seeing results)."""

    anchor_shock_threshold: float = ANCHOR_SHOCK_THRESHOLD
    hold_trading_days: int = HOLD_TRADING_DAYS
    position_pct: float = POSITION_PCT
    max_concurrent: int = MAX_CONCURRENT
    hard_stop_pct: float = HARD_STOP_PCT
    stated_probability: float = STATED_PROBABILITY
    max_peers: int = SECTOR_BENCHMARK_MAX_PEERS
    # The graph lane applies max_concurrent (the only mechanical selection); the
    # mech control twin sets this False to take EVERY observable linked name.
    apply_concurrency_cap: bool = True
    # 9e — additive UP side (default OFF so the shot-8 down-only reproduction is
    # bit-identical). When True, the strategy ALSO detects positive anchor shocks
    # and places a long-tradeable BUY (≤position_pct equity) on the predicted-UP
    # filer, resolving on the SAME sector-neutral excess in a separate up bucket.
    up_enabled: bool = False
    up_anchor_shock_threshold: float = ANCHOR_UP_SHOCK_THRESHOLD


@dataclass
class _Pending:
    """A signal detected on the shock day T, awaiting T+1 entry."""

    filer: str
    edge_type: str
    anchor: str
    move_pct: float
    shock_ts: datetime
    direction: str


@dataclass
class _OpenPrediction:
    """An entered prediction, resolving after `hold_trading_days` trading days."""

    thesis_id: str
    filer: str
    edge_type: str
    bucket: str
    trade_type: str
    anchor: str
    direction: str
    entry_ts: datetime
    entry_filer_price: float
    entry_peer_prices: dict[str, float]
    trading_days_held: int = 0


@dataclass
class _UpPending:
    """A positive-shock signal detected on day T, awaiting a T+1 BUY entry (9e)."""

    filer: str
    edge_type: str
    anchor: str
    move_pct: float
    shock_ts: datetime
    direction: str  # "up"


@dataclass
class _OpenUpPrediction:
    """An entered UP prediction backed by a real (paper) long position (9e).

    Resolves on the SAME sector-neutral excess as the down side (the gate
    metric), and closes the held quantity with a SELL at the hold-window end.
    """

    thesis_id: str
    filer: str
    edge_type: str
    bucket: str
    trade_type: str
    anchor: str
    direction: str
    entry_ts: datetime
    entry_filer_price: float
    entry_peer_prices: dict[str, float]
    quantity: float  # filled BUY quantity to unwind at resolution
    trading_days_held: int = 0


class FrontierPropagationStrategy(Strategy):
    """Mechanical propagation engine. Drives identically in backtest/paper/live.

    Lifecycle of a signal (one tick == one trading day in the daily paper run):
      T   (shock day): detect anchor shocks; for each PIT-valid observable edge
                       from a shocked anchor, queue a `_Pending` signal.
      T+1 (entry):     snapshot filer + sector-peer entry prices, record the
                       thesis in the per-edge-type bucket, open the prediction.
      T+1..+N:         count trading days held.
      T+1+N (resolve): snapshot exit prices, compute sector-neutral excess,
                       resolve the thesis (outcome = sign matches direction).
    """

    def __init__(
        self,
        config: FrontierConfig | None = None,
        *,
        bot_id: str = BOT_ID,
        anchor_shock_detector: AnchorShockDetector | None = None,
        up_anchor_shock_detector: AnchorShockDetector | None = None,
        sector_members: dict[str, list[str]] | None = None,
        price_snapshot: PriceSnapshot | None = None,
        edges: list[dict[str, Any]] | None = None,
    ) -> None:
        self._cfg = config or FrontierConfig()
        self._bot_id = bot_id
        self._detector = anchor_shock_detector
        # 9e: optional UP-shock detector (positive anchor moves). Only used when
        # config.up_enabled; absent -> the down-only behaviour is unchanged.
        self._up_detector = up_anchor_shock_detector
        # filer (upper) -> list of sector peer symbols (the full sector membership).
        self._sector_members = {
            k.upper(): list(v) for k, v in (sector_members or {}).items()
        }
        self._price_snapshot = price_snapshot
        # The pre-verified graph edge set. Injected so the trading DB (paper.db)
        # need not hold the graph (which lives in the graph DB, state.db). When
        # None the strategy falls back to the wired store's frontier_edges.
        self._edges = edges
        self._pending: list[_Pending] = []
        self._open: list[_OpenPrediction] = []
        # 9e: separate up-side state — never commingled with the down side.
        self._up_pending: list[_UpPending] = []
        self._up_open: list[_OpenUpPrediction] = []
        self._run_context: Any = None

    @property
    def bot_id(self) -> str:
        return self._bot_id

    def set_run_context(self, run_context: Any) -> None:
        self._run_context = run_context

    # --- lifecycle --------------------------------------------------------

    def on_tick(self, ctx: StrategyContext) -> None:
        # Order matters: enter yesterday's pending signals at TODAY's price
        # (true T+1), advance/resolve open predictions, THEN detect today's
        # shocks (which enter tomorrow). This mirrors next-bar discipline.
        self._enter_pending(ctx)
        self._advance_and_resolve(ctx)
        self._detect(ctx)
        # 9e: the additive UP side, only when enabled. Same T+1 / hold / resolve
        # discipline, but long-tradeable (places a real paper BUY). Down-only
        # behaviour above is untouched (shot-8 numbers stay reproducible).
        if self._cfg.up_enabled:
            self._enter_up_pending(ctx)
            self._advance_and_resolve_up(ctx)
            self._detect_up(ctx)

    # --- detection (shock day T) -----------------------------------------

    def _detect(self, ctx: StrategyContext) -> None:
        if self._detector is None:
            return
        shocks = self._detector(ctx.now)
        if not shocks:
            return
        edges = self._edges
        if edges is None:
            edges = ctx.store.get_live_frontier_edges(ctx.now, named_only=True)
        live = pit_valid_tradeable_edges(edges, ctx.now)
        by_anchor: dict[str, list[dict[str, Any]]] = {}
        for e in live:
            by_anchor.setdefault(str(e["counterparty_symbol"]).upper(), []).append(e)

        open_and_pending = {p.filer for p in self._pending} | {o.filer for o in self._open}
        for shock in sorted(shocks, key=lambda s: s.anchor_symbol):
            linked = by_anchor.get(shock.anchor_symbol.upper(), [])
            # Deterministic order so the concurrency cap is reproducible.
            for e in sorted(linked, key=lambda x: str(x["from_symbol"])):
                filer = str(e["from_symbol"]).upper()
                direction = predicted_direction(e["edge_type"])
                if direction is None:
                    continue
                if filer in open_and_pending:
                    continue  # one prediction per filer at a time
                self._pending.append(
                    _Pending(
                        filer=filer,
                        edge_type=e["edge_type"],
                        anchor=shock.anchor_symbol.upper(),
                        move_pct=shock.move_pct,
                        shock_ts=shock.as_of,
                        direction=direction,
                    )
                )
                open_and_pending.add(filer)
                ctx.store.record_decision(
                    decision_id=str(uuid.uuid4()),
                    bot_id=self._bot_id,
                    decision_type="signal",
                    action="anchor_shock_detected",
                    inputs={
                        "filer": filer,
                        "anchor": shock.anchor_symbol.upper(),
                        "edge_type": e["edge_type"],
                        "edge_id": e.get("edge_id"),
                        "magnitude_pct": e.get("magnitude_pct"),
                        "anchor_move_pct": shock.move_pct,
                        "predicted_direction": direction,
                        "filed_at": e.get("filed_at"),
                        "strategy_bucket": strategy_bucket_for(e["edge_type"]),
                    },
                    mode=ctx.mode,
                    as_of=ctx.now,
                    run_id=getattr(self._run_context, "run_id", None),
                )

    # --- entry (T+1) ------------------------------------------------------

    def _enter_pending(self, ctx: StrategyContext) -> None:
        if not self._pending:
            return
        ledger = CalibrationLedger(ctx.store)
        still_pending: list[_Pending] = []
        open_concurrent = len(self._open)
        for sig in self._pending:
            if (
                self._cfg.apply_concurrency_cap
                and open_concurrent >= self._cfg.max_concurrent
            ):
                # Graph lane: capped. The signal is dropped (logged), not
                # cherry-picked later — deterministic by filer-symbol order.
                ctx.store.record_decision(
                    decision_id=str(uuid.uuid4()),
                    bot_id=self._bot_id,
                    decision_type="entry",
                    action="skipped_concurrency_cap",
                    inputs={
                        "filer": sig.filer,
                        "anchor": sig.anchor,
                        "edge_type": sig.edge_type,
                        "max_concurrent": self._cfg.max_concurrent,
                        "strategy_bucket": strategy_bucket_for(sig.edge_type),
                    },
                    mode=ctx.mode,
                    as_of=ctx.now,
                    run_id=getattr(self._run_context, "run_id", None),
                )
                continue

            peers = sector_benchmark_peers(
                sig.filer,
                self._sector_members.get(sig.filer, []),
                max_peers=self._cfg.max_peers,
            )
            prices = self._snapshot(ctx, [sig.filer, *peers])
            filer_price = prices.get(sig.filer)
            if not filer_price or filer_price <= 0:
                # No entry price this tick — drop (the shock window has passed).
                continue
            peer_prices = {p: prices[p] for p in peers if prices.get(p, 0) > 0}

            bucket = strategy_bucket_for(sig.edge_type)
            trade_type = trade_type_for(sig.edge_type)
            thesis_id = str(uuid.uuid4())
            ledger.record_prediction(
                thesis_id=thesis_id,
                bot_id=self._bot_id,
                symbol=sig.filer,
                direction=sig.direction,
                stated_probability=self._cfg.stated_probability,
                trade_type=trade_type,
                mode=ctx.mode,
                rationale=(
                    f"Frontier propagation: anchor {sig.anchor} shock "
                    f"{sig.move_pct:+.1%} on {sig.shock_ts.date().isoformat()} -> "
                    f"predict {sig.filer} {sig.direction} (sector-neutral, "
                    f"{sig.edge_type}, hold {self._cfg.hold_trading_days}d)"
                ),
                strategy_bucket=bucket,
            )
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="entry",
                action="prediction_opened",
                inputs={
                    "filer": sig.filer,
                    "anchor": sig.anchor,
                    "edge_type": sig.edge_type,
                    "predicted_direction": sig.direction,
                    "entry_price": filer_price,
                    "peer_count": len(peer_prices),
                    "stated_probability": self._cfg.stated_probability,
                    "strategy_bucket": bucket,
                },
                mode=ctx.mode,
                as_of=ctx.now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=thesis_id,
            )
            self._open.append(
                _OpenPrediction(
                    thesis_id=thesis_id,
                    filer=sig.filer,
                    edge_type=sig.edge_type,
                    bucket=bucket,
                    trade_type=trade_type,
                    anchor=sig.anchor,
                    direction=sig.direction,
                    entry_ts=ctx.now,
                    entry_filer_price=filer_price,
                    entry_peer_prices=peer_prices,
                )
            )
            open_concurrent += 1
        self._pending = still_pending

    # --- advance + resolve (T+1+N) ---------------------------------------

    def _advance_and_resolve(self, ctx: StrategyContext) -> None:
        if not self._open:
            return
        ledger = CalibrationLedger(ctx.store)
        still_open: list[_OpenPrediction] = []
        for pred in self._open:
            # This tick is a new trading day for an already-open prediction.
            pred.trading_days_held += 1
            if pred.trading_days_held < self._cfg.hold_trading_days:
                still_open.append(pred)
                continue
            symbols = [pred.filer, *pred.entry_peer_prices.keys()]
            prices = self._snapshot(ctx, symbols)
            filer_now = prices.get(pred.filer)
            if not filer_now or filer_now <= 0:
                still_open.append(pred)  # no exit quote yet; resolve next tick
                continue
            filer_return = (filer_now - pred.entry_filer_price) / pred.entry_filer_price
            peer_returns: list[float] = []
            for p, entry_px in pred.entry_peer_prices.items():
                now_px = prices.get(p)
                if now_px and now_px > 0 and entry_px > 0:
                    peer_returns.append((now_px - entry_px) / entry_px)
            excess = sector_neutral_excess(filer_return, peer_returns)
            if excess is None:
                # No peer benchmark available — fall back to raw return so the
                # prediction still resolves (flagged); sector-neutral is the
                # gate's definition and is what the backtest reports.
                excess = filer_return
                sector_neutral = False
            else:
                sector_neutral = True
            outcome = sign_is_correct(pred.direction, excess)
            ledger.resolve(
                thesis_id=pred.thesis_id,
                bot_id=self._bot_id,
                trade_type=pred.trade_type,
                stated_probability=self._cfg.stated_probability,
                outcome=outcome,
                bucket=ledger.bucket_for_probability(self._cfg.stated_probability),
                resolved_at=ctx.now,
                strategy_bucket=pred.bucket,
            )
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="resolution",
                action="sign_correct" if outcome else "sign_wrong",
                inputs={
                    "filer": pred.filer,
                    "anchor": pred.anchor,
                    "edge_type": pred.edge_type,
                    "predicted_direction": pred.direction,
                    "entry_price": pred.entry_filer_price,
                    "exit_price": filer_now,
                    "filer_return": filer_return,
                    "excess_return": excess,
                    "sector_neutral": sector_neutral,
                    "peer_count": len(peer_returns),
                    "trading_days_held": pred.trading_days_held,
                    "strategy_bucket": pred.bucket,
                },
                mode=ctx.mode,
                as_of=ctx.now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=pred.thesis_id,
            )
        self._open = still_open

    # --- UP side (9e): detect -> BUY entry T+1 -> resolve + SELL ----------

    def _detect_up(self, ctx: StrategyContext) -> None:
        if self._up_detector is None:
            return
        shocks = self._up_detector(ctx.now)
        if not shocks:
            return
        edges = self._edges
        if edges is None:
            edges = ctx.store.get_live_frontier_edges(ctx.now, named_only=True)
        live = pit_valid_tradeable_edges(edges, ctx.now)
        by_anchor: dict[str, list[dict[str, Any]]] = {}
        for e in live:
            by_anchor.setdefault(str(e["counterparty_symbol"]).upper(), []).append(e)

        busy = (
            {p.filer for p in self._up_pending}
            | {o.filer for o in self._up_open}
        )
        for shock in sorted(shocks, key=lambda s: s.anchor_symbol):
            linked = by_anchor.get(shock.anchor_symbol.upper(), [])
            for e in sorted(linked, key=lambda x: str(x["from_symbol"])):
                filer = str(e["from_symbol"]).upper()
                direction = up_predicted_direction(e["edge_type"])
                if direction is None:
                    continue
                if filer in busy:
                    continue
                self._up_pending.append(
                    _UpPending(
                        filer=filer,
                        edge_type=e["edge_type"],
                        anchor=shock.anchor_symbol.upper(),
                        move_pct=shock.move_pct,
                        shock_ts=shock.as_of,
                        direction=direction,
                    )
                )
                busy.add(filer)
                ctx.store.record_decision(
                    decision_id=str(uuid.uuid4()),
                    bot_id=self._bot_id,
                    decision_type="signal",
                    action="anchor_up_shock_detected",
                    inputs={
                        "filer": filer,
                        "anchor": shock.anchor_symbol.upper(),
                        "edge_type": e["edge_type"],
                        "edge_id": e.get("edge_id"),
                        "anchor_move_pct": shock.move_pct,
                        "predicted_direction": direction,
                        "filed_at": e.get("filed_at"),
                        "strategy_bucket": up_strategy_bucket_for(e["edge_type"]),
                    },
                    mode=ctx.mode,
                    as_of=ctx.now,
                    run_id=getattr(self._run_context, "run_id", None),
                )

    def _enter_up_pending(self, ctx: StrategyContext) -> None:
        if not self._up_pending:
            return
        ledger = CalibrationLedger(ctx.store)
        open_concurrent = len(self._up_open)
        for sig in self._up_pending:
            if (
                self._cfg.apply_concurrency_cap
                and open_concurrent >= self._cfg.max_concurrent
            ):
                ctx.store.record_decision(
                    decision_id=str(uuid.uuid4()),
                    bot_id=self._bot_id,
                    decision_type="entry",
                    action="skipped_concurrency_cap_up",
                    inputs={
                        "filer": sig.filer,
                        "anchor": sig.anchor,
                        "edge_type": sig.edge_type,
                        "max_concurrent": self._cfg.max_concurrent,
                        "strategy_bucket": up_strategy_bucket_for(sig.edge_type),
                    },
                    mode=ctx.mode,
                    as_of=ctx.now,
                    run_id=getattr(self._run_context, "run_id", None),
                )
                continue

            peers = sector_benchmark_peers(
                sig.filer,
                self._sector_members.get(sig.filer, []),
                max_peers=self._cfg.max_peers,
            )
            prices = self._snapshot(ctx, [sig.filer, *peers])
            filer_price = prices.get(sig.filer)
            if not filer_price or filer_price <= 0:
                continue  # no entry price this tick — drop (shock window passed)
            peer_prices = {p: prices[p] for p in peers if prices.get(p, 0) > 0}

            # Size a small LONG, ≤ position_pct of equity (mirrors the down
            # side's sizing). Long-only cash account: a normal BUY.
            equity = ctx.broker.get_portfolio(ctx.now).total_equity
            quantity = round((equity * self._cfg.position_pct) / filer_price, 4)
            if quantity <= 0:
                continue

            bucket = up_strategy_bucket_for(sig.edge_type)
            trade_type = up_trade_type_for(sig.edge_type)
            thesis_id = str(uuid.uuid4())
            ledger.record_prediction(
                thesis_id=thesis_id,
                bot_id=self._bot_id,
                symbol=sig.filer,
                direction=sig.direction,
                stated_probability=self._cfg.stated_probability,
                trade_type=trade_type,
                mode=ctx.mode,
                rationale=(
                    f"Frontier UP propagation: anchor {sig.anchor} shock "
                    f"{sig.move_pct:+.1%} on {sig.shock_ts.date().isoformat()} -> "
                    f"BUY {sig.filer} (predict {sig.direction}, sector-neutral, "
                    f"{sig.edge_type}, hold {self._cfg.hold_trading_days}d)"
                ),
                strategy_bucket=bucket,
            )
            order = OrderRequest(
                symbol=sig.filer,
                side=Side.BUY,
                quantity=quantity,
                order_type=OrderType.MARKET,
                bot_id=self._bot_id,
                thesis_id=thesis_id,
            )
            result = ctx.submit_and_record(order)
            filled_qty = quantity if result.status != OrderStatus.REJECTED else 0.0
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="entry",
                action="up_prediction_opened",
                inputs={
                    "filer": sig.filer,
                    "anchor": sig.anchor,
                    "edge_type": sig.edge_type,
                    "predicted_direction": sig.direction,
                    "entry_price": filer_price,
                    "quantity": quantity,
                    "order_status": result.status.value,
                    "equity": equity,
                    "peer_count": len(peer_prices),
                    "stated_probability": self._cfg.stated_probability,
                    "strategy_bucket": bucket,
                },
                mode=ctx.mode,
                as_of=ctx.now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=thesis_id,
            )
            self._up_open.append(
                _OpenUpPrediction(
                    thesis_id=thesis_id,
                    filer=sig.filer,
                    edge_type=sig.edge_type,
                    bucket=bucket,
                    trade_type=trade_type,
                    anchor=sig.anchor,
                    direction=sig.direction,
                    entry_ts=ctx.now,
                    entry_filer_price=filer_price,
                    entry_peer_prices=peer_prices,
                    quantity=filled_qty,
                )
            )
            open_concurrent += 1
        self._up_pending = []

    def _advance_and_resolve_up(self, ctx: StrategyContext) -> None:
        if not self._up_open:
            return
        ledger = CalibrationLedger(ctx.store)
        still_open: list[_OpenUpPrediction] = []
        for pred in self._up_open:
            pred.trading_days_held += 1
            if pred.trading_days_held < self._cfg.hold_trading_days:
                still_open.append(pred)
                continue
            symbols = [pred.filer, *pred.entry_peer_prices.keys()]
            prices = self._snapshot(ctx, symbols)
            filer_now = prices.get(pred.filer)
            if not filer_now or filer_now <= 0:
                still_open.append(pred)  # no exit quote yet; resolve next tick
                continue
            filer_return = (filer_now - pred.entry_filer_price) / pred.entry_filer_price
            peer_returns: list[float] = []
            for p, entry_px in pred.entry_peer_prices.items():
                now_px = prices.get(p)
                if now_px and now_px > 0 and entry_px > 0:
                    peer_returns.append((now_px - entry_px) / entry_px)
            excess = sector_neutral_excess(filer_return, peer_returns)
            if excess is None:
                excess = filer_return
                sector_neutral = False
            else:
                sector_neutral = True
            outcome = sign_is_correct(pred.direction, excess)
            ledger.resolve(
                thesis_id=pred.thesis_id,
                bot_id=self._bot_id,
                trade_type=pred.trade_type,
                stated_probability=self._cfg.stated_probability,
                outcome=outcome,
                bucket=ledger.bucket_for_probability(self._cfg.stated_probability),
                resolved_at=ctx.now,
                strategy_bucket=pred.bucket,
            )
            # Close the real (paper) long position, if one was opened.
            if pred.quantity > 0:
                ctx.submit_and_record(
                    OrderRequest(
                        symbol=pred.filer,
                        side=Side.SELL,
                        quantity=pred.quantity,
                        order_type=OrderType.MARKET,
                        bot_id=self._bot_id,
                        thesis_id=pred.thesis_id,
                    )
                )
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="resolution",
                action="sign_correct_up" if outcome else "sign_wrong_up",
                inputs={
                    "filer": pred.filer,
                    "anchor": pred.anchor,
                    "edge_type": pred.edge_type,
                    "predicted_direction": pred.direction,
                    "entry_price": pred.entry_filer_price,
                    "exit_price": filer_now,
                    "filer_return": filer_return,
                    "excess_return": excess,
                    "sector_neutral": sector_neutral,
                    "peer_count": len(peer_returns),
                    "quantity": pred.quantity,
                    "trading_days_held": pred.trading_days_held,
                    "strategy_bucket": pred.bucket,
                },
                mode=ctx.mode,
                as_of=ctx.now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=pred.thesis_id,
            )
        self._up_open = still_open

    # --- price snapshot ---------------------------------------------------

    def _snapshot(self, ctx: StrategyContext, symbols: list[str]) -> dict[str, float]:
        if self._price_snapshot is not None:
            return self._price_snapshot(symbols)
        out: dict[str, float] = {}
        for s in symbols:
            try:
                q = ctx.datafeed.get_quote(s)
            except Exception:  # noqa: BLE001 - per-symbol tolerance (missing peer ok)
                continue
            if q.price and q.price > 0:
                out[s] = q.price
        return out

    # --- state round-trip for the daily one-tick paper process ------------

    def export_state(self) -> dict[str, Any]:
        return {
            "pending": [
                {
                    "filer": p.filer,
                    "edge_type": p.edge_type,
                    "anchor": p.anchor,
                    "move_pct": p.move_pct,
                    "shock_ts": p.shock_ts.isoformat(),
                    "direction": p.direction,
                }
                for p in self._pending
            ],
            "open": [
                {
                    "thesis_id": o.thesis_id,
                    "filer": o.filer,
                    "edge_type": o.edge_type,
                    "bucket": o.bucket,
                    "trade_type": o.trade_type,
                    "anchor": o.anchor,
                    "direction": o.direction,
                    "entry_ts": o.entry_ts.isoformat(),
                    "entry_filer_price": o.entry_filer_price,
                    "entry_peer_prices": o.entry_peer_prices,
                    "trading_days_held": o.trading_days_held,
                }
                for o in self._open
            ],
            "up_pending": [
                {
                    "filer": p.filer,
                    "edge_type": p.edge_type,
                    "anchor": p.anchor,
                    "move_pct": p.move_pct,
                    "shock_ts": p.shock_ts.isoformat(),
                    "direction": p.direction,
                }
                for p in self._up_pending
            ],
            "up_open": [
                {
                    "thesis_id": o.thesis_id,
                    "filer": o.filer,
                    "edge_type": o.edge_type,
                    "bucket": o.bucket,
                    "trade_type": o.trade_type,
                    "anchor": o.anchor,
                    "direction": o.direction,
                    "entry_ts": o.entry_ts.isoformat(),
                    "entry_filer_price": o.entry_filer_price,
                    "entry_peer_prices": o.entry_peer_prices,
                    "quantity": o.quantity,
                    "trading_days_held": o.trading_days_held,
                }
                for o in self._up_open
            ],
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        self._pending = [
            _Pending(
                filer=r["filer"],
                edge_type=r["edge_type"],
                anchor=r["anchor"],
                move_pct=float(r["move_pct"]),
                shock_ts=datetime.fromisoformat(r["shock_ts"]),
                direction=r["direction"],
            )
            for r in state.get("pending", [])
        ]
        self._open = [
            _OpenPrediction(
                thesis_id=r["thesis_id"],
                filer=r["filer"],
                edge_type=r["edge_type"],
                bucket=r["bucket"],
                trade_type=r["trade_type"],
                anchor=r["anchor"],
                direction=r["direction"],
                entry_ts=datetime.fromisoformat(r["entry_ts"]),
                entry_filer_price=float(r["entry_filer_price"]),
                entry_peer_prices={k: float(v) for k, v in r.get("entry_peer_prices", {}).items()},
                trading_days_held=int(r.get("trading_days_held", 0)),
            )
            for r in state.get("open", [])
        ]
        self._up_pending = [
            _UpPending(
                filer=r["filer"],
                edge_type=r["edge_type"],
                anchor=r["anchor"],
                move_pct=float(r["move_pct"]),
                shock_ts=datetime.fromisoformat(r["shock_ts"]),
                direction=r["direction"],
            )
            for r in state.get("up_pending", [])
        ]
        self._up_open = [
            _OpenUpPrediction(
                thesis_id=r["thesis_id"],
                filer=r["filer"],
                edge_type=r["edge_type"],
                bucket=r["bucket"],
                trade_type=r["trade_type"],
                anchor=r["anchor"],
                direction=r["direction"],
                entry_ts=datetime.fromisoformat(r["entry_ts"]),
                entry_filer_price=float(r["entry_filer_price"]),
                entry_peer_prices={k: float(v) for k, v in r.get("entry_peer_prices", {}).items()},
                quantity=float(r.get("quantity", 0.0)),
                trading_days_held=int(r.get("trading_days_held", 0)),
            )
            for r in state.get("up_open", [])
        ]


# =============================================================================
# Live wiring (daily paper tick) — batched Tiingo IEX, per-day cached
# =============================================================================

TIINGO_IEX_URL = "https://api.tiingo.com/iex/"
_IEX_BATCH_SIZE = 50


def _iex_last_prices(client: Any, symbols: list[str], tiingo_api_key: str) -> dict[str, dict[str, float]]:
    """Batched IEX read: {symbol: {"last": x, "prevClose": y}} for what's available."""
    out: dict[str, dict[str, float]] = {}
    uniq = sorted({s.upper() for s in symbols})
    for off in range(0, len(uniq), _IEX_BATCH_SIZE):
        chunk = uniq[off : off + _IEX_BATCH_SIZE]
        try:
            rows = client.get_json(
                TIINGO_IEX_URL, params={"tickers": ",".join(chunk), "token": tiingo_api_key}
            )
        except Exception as exc:  # noqa: BLE001 - per-batch isolation
            logger.warning("frontier IEX batch failed (%s..): %s", chunk[0], exc)
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            sym = str(row.get("ticker", "")).upper()
            last = row.get("tngoLast") or row.get("last")
            prev = row.get("prevClose")
            if not sym:
                continue
            entry: dict[str, float] = {}
            if last:
                entry["last"] = float(last)
            if prev:
                entry["prevClose"] = float(prev)
            if entry:
                out[sym] = entry
    return out


class LiveAnchorShockDetector:
    """Detects today's down-shocks on the OBSERVABLE anchor set for the paper tick.

    One batched Tiingo IEX read over the distinct resolved counterparties in the
    live graph; a shock is a close-to-close (prevClose -> last) drop that clears
    the pre-registered threshold. Computed once per (process, day) and cached so
    the graph lane and the mech twin see the identical shock list at zero extra
    cost — the same pattern as the 2c quality gate / 2.5a crash detector.
    """

    def __init__(
        self,
        *,
        anchors: list[str],
        client: Any,
        tiingo_api_key: str,
        threshold: float = ANCHOR_SHOCK_THRESHOLD,
    ) -> None:
        self._anchors = sorted({a.upper() for a in anchors})
        self._client = client
        self._tiingo_key = tiingo_api_key
        self._threshold = threshold
        self._cache: dict[str, list[AnchorShock]] = {}

    def detect(self, now: datetime) -> list[AnchorShock]:
        day = now.date().isoformat()
        if day in self._cache:
            return self._cache[day]
        prices = _iex_last_prices(self._client, self._anchors, self._tiingo_key)
        shocks: list[AnchorShock] = []
        for anchor in self._anchors:
            row = prices.get(anchor)
            if not row or "last" not in row or "prevClose" not in row:
                continue
            prev, last = row["prevClose"], row["last"]
            if is_anchor_shock(prev, last):
                shocks.append(
                    AnchorShock(
                        anchor_symbol=anchor,
                        as_of=now,
                        prev_close=prev,
                        close=last,
                        move_pct=(last - prev) / prev,
                    )
                )
        self._cache[day] = shocks
        if shocks:
            logger.info(
                "frontier anchor detector: %d shock(s) on %s: %s",
                len(shocks), day, ", ".join(f"{s.anchor_symbol} {s.move_pct:+.1%}" for s in shocks),
            )
        return shocks


class LiveAnchorUpShockDetector:
    """9e — detects today's UP-shocks on the OBSERVABLE anchor set (paper tick).

    Mirror of ``LiveAnchorShockDetector`` for positive moves: a close-to-close
    (prevClose -> last) RISE that clears the pre-registered up threshold. Same
    batched IEX read and per-(process, day) cache so the graph lane and the mech
    twin see the identical up-shock list at zero extra cost.
    """

    def __init__(
        self,
        *,
        anchors: list[str],
        client: Any,
        tiingo_api_key: str,
        threshold: float = ANCHOR_UP_SHOCK_THRESHOLD,
    ) -> None:
        self._anchors = sorted({a.upper() for a in anchors})
        self._client = client
        self._tiingo_key = tiingo_api_key
        self._threshold = threshold
        self._cache: dict[str, list[AnchorShock]] = {}

    def detect(self, now: datetime) -> list[AnchorShock]:
        day = now.date().isoformat()
        if day in self._cache:
            return self._cache[day]
        prices = _iex_last_prices(self._client, self._anchors, self._tiingo_key)
        shocks: list[AnchorShock] = []
        for anchor in self._anchors:
            row = prices.get(anchor)
            if not row or "last" not in row or "prevClose" not in row:
                continue
            prev, last = row["prevClose"], row["last"]
            if is_anchor_up_shock(prev, last):
                shocks.append(
                    AnchorShock(
                        anchor_symbol=anchor,
                        as_of=now,
                        prev_close=prev,
                        close=last,
                        move_pct=(last - prev) / prev,
                    )
                )
        self._cache[day] = shocks
        if shocks:
            logger.info(
                "frontier anchor UP detector: %d shock(s) on %s: %s",
                len(shocks), day, ", ".join(f"{s.anchor_symbol} {s.move_pct:+.1%}" for s in shocks),
            )
        return shocks


class LivePriceSnapshot:
    """Batched IEX `last` snapshot for the strategy's sector-neutral resolution.

    Cached per (process, requested-symbol-set) so repeated peer lookups within a
    tick don't re-spend. Falls back gracefully to whatever IEX returns.
    """

    def __init__(self, *, client: Any, tiingo_api_key: str) -> None:
        self._client = client
        self._tiingo_key = tiingo_api_key
        self._cache: dict[str, float] = {}

    def __call__(self, symbols: list[str]) -> dict[str, float]:
        missing = [s.upper() for s in symbols if s.upper() not in self._cache]
        if missing:
            rows = _iex_last_prices(self._client, missing, self._tiingo_key)
            for sym, row in rows.items():
                px = row.get("last") or row.get("prevClose")
                if px and px > 0:
                    self._cache[sym] = px
        return {s.upper(): self._cache[s.upper()] for s in symbols if s.upper() in self._cache}

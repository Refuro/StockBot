"""Proportionality Engine, ride side — drift strategy (Tickets 2b/2c/2d).

The MECHANICAL rule is unchanged from the 2b checkpoint:

- Entry: long a positive earnings surprise that clears the entry rule.
  - ``top_decile`` (primary, pre-registered): surprise >= the 90th percentile
    of all surprises observed *strictly before* this event (expanding window —
    point-in-time by construction; no entries until enough history accrues).
  - ``threshold`` (secondary cuts): surprise > fixed threshold.
- Default exit: time-stop after ``hold_days`` calendar days.
- Every entry records a thesis tagged ``strategy_bucket="earnings:ride"``;
  theses resolve on the realized round trip, feeding the calibration ledger.
- All orders go through ``StrategyContext.submit_and_record`` — risk is
  enforced centrally there.

2c adds an OPTIONAL quality gate: an injected callable that scores the
qualifying event (LLM ensemble, see ``quality.py``) and returns a stated
probability. The gate only filters entries and supplies the thesis
probability — it never sizes orders and never touches the risk engine. With
no gate (the default, and the mechanical-control twin), behaviour is
bit-identical to 2b: constant 0.55.

2d adds OPTIONAL exit rules (``exit_rules_enabled``, default OFF so the 2b
backtests replay unchanged): profit-take 50% at +15%, 7% trailing stop,
-8% hard stop, 14-day time-stop.

2.5c grows the FADE side in this same class (one engine, two trade types —
never a fork). Config-gated (``fade_enabled``, default OFF so every existing
lane is bit-identical):

- Entry: a detected crash (2.5a ``CrashEvent``) graded "overreaction" by the
  2.5b fade gate (rubric + mandatory counter-pass). With no gate (the
  ``crash_fade_mech`` control twin), EVERY detected crash is faded at the
  constant 0.55 — the brain-vs-coin race, mirroring the ride race.
- Pre-registered exits (plan row 2.5c): -8% hard stop (protective, immediate),
  10-day time-stop; minimum hold 2 days applies to any non-stop exit (none
  exist today, the floor is enforced for future ones).
- Sizing: <=3% of portfolio equity per fade, max 2 concurrent fades.
- Every fade thesis lands in ``strategy_bucket="crash:fade"`` with the real
  stated probability; the counter-pass result is logged on EVERY fade
  decision, entered or vetoed (hard rule 3).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from stockbot.bots.proportionality.crash import CrashEvent
from stockbot.bots.proportionality.watcher import (
    EarningsSurprise,
    WatcherStats,
    extract_earnings_surprises,
)
from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.strategy import Strategy, StrategyContext
from stockbot.types import OrderRequest, OrderStatus, OrderType, Side

BOT_ID = "proportionality"
EVENT_TYPE = "earnings"
DIRECTION = "ride"
TRADE_TYPE = "earnings_ride"

# 2.5c fade side: bucket crash:fade, trade type crash_fade.
FADE_EVENT_TYPE = "crash"
FADE_DIRECTION = "fade"
FADE_TRADE_TYPE = "crash_fade"

# 2c quality gate: takes the validated surprise event, returns an assessment
# (see quality.QualityAssessment) or None when scoring was impossible.
QualityGate = Callable[[EarningsSurprise], Any]

# 2.5c: crash detector (now -> today's CrashEvents) and fade gate
# (CrashEvent -> fade.FadeAssessment). Gate None = mechanical control twin.
CrashDetector = Callable[[datetime], list[CrashEvent]]
FadeGate = Callable[[CrashEvent], Any]


@dataclass(frozen=True)
class RideConfig:
    """Pre-registered mechanical parameters (do not tune after seeing results)."""

    entry_mode: str = "top_decile"  # top_decile | threshold
    surprise_threshold: float = 0.05  # threshold mode only
    decile_pct: float = 0.90
    min_history_for_decile: int = 40
    hold_days: int = 10  # calendar days (time-stop when exit rules are OFF)
    target_notional: float = 4_000.0
    max_open_positions: int = 20
    stated_probability: float = 0.55  # mechanical constant; calibrates the bucket plumbing
    symbol_allowlist: frozenset[str] | None = None  # ADV-filtered universe, if provided

    # 2d exit rules (from the plan, fixed): default OFF so 2b replays unchanged.
    exit_rules_enabled: bool = False
    profit_take_trigger_pct: float = 0.15   # +15% -> take profit on half
    profit_take_fraction: float = 0.5
    trailing_stop_pct: float = 0.07         # 7% off the high-water mark
    hard_stop_pct: float = 0.08             # -8% from entry
    time_stop_days: int = 14                # calendar days

    # 2.5c fade side (plan row 2.5c, fixed): defaults keep every existing lane
    # bit-identical — ride only, no fades.
    ride_enabled: bool = True               # crash_fade_mech twin turns this OFF
    fade_enabled: bool = False
    # Shot 11: the calibration direction for fade theses. Default "fade" keeps
    # the v1 gated lane + crash_fade_mech booking bucket "crash:fade"
    # (bit-identical); the additive crash_fade_v2 lane sets "fade_v2" so its
    # head-to-head accrues in its own "crash:fade_v2" bucket.
    fade_direction: str = FADE_DIRECTION
    fade_max_concurrent: int = 2
    fade_position_pct: float = 0.03         # <=3% of portfolio equity per fade
    fade_hard_stop_pct: float = 0.08        # -8% from entry (protective, immediate)
    fade_time_stop_days: int = 10           # calendar days
    fade_min_hold_days: int = 2             # floor for any non-stop exit
    fade_stated_probability: float = 0.55   # mech-twin constant (no gate)

    def __post_init__(self) -> None:
        if self.entry_mode not in {"top_decile", "threshold"}:
            raise ValueError("entry_mode must be 'top_decile' or 'threshold'")


# 2-R1: ticks to wait for a filled-but-missing position before abandoning loudly.
MISSING_POSITION_RETRY_TICKS = 3


@dataclass
class _OpenTrade:
    symbol: str
    thesis_id: str
    entry_order_id: str
    entry_ts: datetime
    event_id: str
    missing_position_ticks: int = 0
    stated_probability: float = 0.55
    trade_kind: str = "ride"           # ride | fade (2.5c)
    # 2d exit-rule state:
    entry_price: float | None = None   # from the entry fill, once settled
    high_water: float | None = None
    profit_taken: bool = False
    exit_order_ids: list[str] = field(default_factory=list)  # partial exits


@dataclass
class _ClosingTrade:
    symbol: str
    thesis_id: str
    entry_order_id: str
    exit_order_ids: list[str]
    event_id: str
    stated_probability: float = 0.55
    trade_kind: str = "ride"


def percentile_nearest_rank(values: list[float], pct: float) -> float:
    """Nearest-rank percentile of `values` (no interpolation)."""
    if not values:
        raise ValueError("percentile of empty list")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


class ProportionalityRideStrategy(Strategy):
    """Mechanical PEAD ride side. Drives identically in backtest/paper/live."""

    def __init__(
        self,
        config: RideConfig | None = None,
        bot_id: str = BOT_ID,
        quality_gate: QualityGate | None = None,
        crash_detector: CrashDetector | None = None,
        fade_gate: FadeGate | None = None,
    ) -> None:
        self._cfg = config or RideConfig()
        self._bot_id = bot_id
        self._bucket = CalibrationLedger.strategy_bucket(EVENT_TYPE, DIRECTION)
        self._fade_bucket = CalibrationLedger.strategy_bucket(
            FADE_EVENT_TYPE, self._cfg.fade_direction
        )
        self._surprise_history: list[float] = []
        self._open: dict[str, _OpenTrade] = {}
        self._closing: list[_ClosingTrade] = []
        self._watcher_stats = WatcherStats()
        self._run_context: Any = None
        self._quality_gate = quality_gate
        self._crash_detector = crash_detector
        self._fade_gate = fade_gate

    @property
    def bot_id(self) -> str:
        return self._bot_id

    @property
    def watcher_stats(self) -> WatcherStats:
        return self._watcher_stats

    def set_run_context(self, run_context: Any) -> None:
        self._run_context = run_context

    # --- entry rule -------------------------------------------------------

    def _entry_signal(self, surprise: float) -> tuple[bool, dict[str, Any]]:
        """Evaluate the pre-registered entry rule on PRIOR history only."""
        if surprise <= 0:
            return False, {"reason": "non_positive_surprise"}
        if self._cfg.entry_mode == "threshold":
            passed = surprise > self._cfg.surprise_threshold
            return passed, {
                "rule": "threshold",
                "threshold": self._cfg.surprise_threshold,
            }
        prior_n = len(self._surprise_history)
        if prior_n < self._cfg.min_history_for_decile:
            return False, {"rule": "top_decile", "reason": "insufficient_history", "prior_n": prior_n}
        cutoff = percentile_nearest_rank(self._surprise_history, self._cfg.decile_pct)
        return surprise >= cutoff, {"rule": "top_decile", "cutoff": cutoff, "prior_n": prior_n}

    # --- lifecycle --------------------------------------------------------

    def on_tick(self, ctx: StrategyContext) -> None:
        self._resolve_closed_trades(ctx)
        if self._cfg.ride_enabled:
            self._process_entries(ctx)
        if self._cfg.fade_enabled and self._crash_detector is not None:
            self._process_fade_entries(ctx)
        self._process_exits(ctx)

    def _process_entries(self, ctx: StrategyContext) -> None:
        events = ctx.datafeed.get_events()
        surprises = extract_earnings_surprises(events, self._watcher_stats)
        ledger = CalibrationLedger(ctx.store)
        for surprise_event in surprises:
            allowed = (
                self._cfg.symbol_allowlist is None
                or surprise_event.symbol in self._cfg.symbol_allowlist
            )
            signal = False
            signal_info: dict[str, Any] = {}
            if allowed:
                signal, signal_info = self._entry_signal(surprise_event.surprise_pct)
            # History grows AFTER the eligibility check: the current event never
            # informs its own decile cutoff (point-in-time discipline). Only
            # allowlisted symbols feed the distribution the rule is defined on.
            if allowed:
                self._surprise_history.append(surprise_event.surprise_pct)
            if not signal:
                continue
            if surprise_event.symbol in self._open or any(
                c.symbol == surprise_event.symbol for c in self._closing
            ):
                continue
            if len(self._open) >= self._cfg.max_open_positions:
                continue

            # 2c: the quality gate filters mechanical entries and supplies the
            # stated probability. The gate may raise LLMSpendCapExceeded — that
            # propagates (hard rule: never improvise around a spend cap).
            stated_probability = self._cfg.stated_probability
            quality_inputs: dict[str, Any] = {}
            quality_rationale = ""
            gate_prompt_version: str | None = None
            if self._quality_gate is not None:
                assessment = self._quality_gate(surprise_event)
                quality_inputs = {
                    "scored": bool(getattr(assessment, "scored", False)),
                    "quality_score": getattr(assessment, "quality_score", None),
                    "stated_probability": getattr(assessment, "stated_probability", None),
                    "flags": list(getattr(assessment, "flags", []) or []),
                    "triage": getattr(assessment, "triage", {}),
                }
                gate_prompt_version = getattr(assessment, "prompt_version", None)
                if assessment is None or not getattr(assessment, "passes_entry_threshold", False):
                    ctx.store.record_decision(
                        decision_id=str(uuid.uuid4()),
                        bot_id=self._bot_id,
                        decision_type="entry",
                        action="skipped_quality_gate",
                        inputs={
                            "event_id": surprise_event.event_id,
                            "symbol": surprise_event.symbol,
                            "surprise_pct": surprise_event.surprise_pct,
                            "signal": signal_info,
                            "quality": quality_inputs,
                            "strategy_bucket": self._bucket,
                        },
                        mode=ctx.mode,
                        as_of=ctx.now,
                        run_id=getattr(self._run_context, "run_id", None),
                        prompt_version=gate_prompt_version,
                    )
                    continue
                stated_probability = float(assessment.stated_probability)
                flags = list(getattr(assessment, "flags", []) or [])
                flag_note = f" [{','.join(flags)}]" if flags else ""
                quality_rationale = f" | quality: {assessment.rationale}{flag_note}"

            try:
                quote = ctx.datafeed.get_quote(surprise_event.symbol)
            except ValueError:
                continue
            if quote.price <= 0:
                continue
            ctx.set_mark(surprise_event.symbol, quote.price)
            quantity = round(self._cfg.target_notional / quote.price, 4)
            if quantity <= 0:
                continue

            thesis_id = str(uuid.uuid4())
            ledger.record_prediction(
                thesis_id=thesis_id,
                bot_id=self._bot_id,
                symbol=surprise_event.symbol,
                direction="long",
                stated_probability=stated_probability,
                trade_type=TRADE_TYPE,
                mode=ctx.mode,
                rationale=(
                    f"Mechanical ride entry: surprise {surprise_event.surprise_pct:+.4f} "
                    f"passed {signal_info.get('rule', self._cfg.entry_mode)}"
                    f"{quality_rationale}"
                ),
                strategy_bucket=self._bucket,
            )
            order = OrderRequest(
                symbol=surprise_event.symbol,
                side=Side.BUY,
                quantity=quantity,
                order_type=OrderType.MARKET,
                bot_id=self._bot_id,
                thesis_id=thesis_id,
            )
            result = ctx.submit_and_record(order)
            entry_inputs: dict[str, Any] = {
                "event_id": surprise_event.event_id,
                "symbol": surprise_event.symbol,
                "surprise_pct": surprise_event.surprise_pct,
                "actual_eps": surprise_event.actual_eps,
                "consensus_eps": surprise_event.consensus_eps,
                "signal": signal_info,
                "quote_price": quote.price,
                "quantity": quantity,
                "order_status": result.status.value,
                "strategy_bucket": self._bucket,
                "stated_probability": stated_probability,
            }
            if quality_inputs:
                entry_inputs["quality"] = quality_inputs
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="entry",
                action="buy" if result.status != OrderStatus.REJECTED else "rejected",
                inputs=entry_inputs,
                mode=ctx.mode,
                as_of=ctx.now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=thesis_id,
                prompt_version=gate_prompt_version,
            )
            if result.status == OrderStatus.REJECTED:
                continue
            self._open[surprise_event.symbol] = _OpenTrade(
                symbol=surprise_event.symbol,
                thesis_id=thesis_id,
                entry_order_id=result.order_id,
                entry_ts=ctx.now,
                event_id=surprise_event.event_id,
                stated_probability=stated_probability,
            )

    # --- 2.5c fade entries ----------------------------------------------------

    def _process_fade_entries(self, ctx: StrategyContext) -> None:
        """Buy graded overreactions (or every crash, for the mech twin)."""
        crashes = self._crash_detector(ctx.now)
        if not crashes:
            return
        ledger = CalibrationLedger(ctx.store)
        for ev in crashes:
            if ev.symbol in self._open or any(c.symbol == ev.symbol for c in self._closing):
                continue
            open_fades = sum(1 for t in self._open.values() if t.trade_kind == "fade")
            if open_fades >= self._cfg.fade_max_concurrent:
                break  # crashes are deterministically ordered; stop, don't cherry-pick

            crash_inputs: dict[str, Any] = {
                "event_id": ev.event_id,
                "symbol": ev.symbol,
                "drop_pct": ev.drop_pct,
                "prev_close": ev.prev_close,
                "close": ev.close,
                "market_cap": ev.market_cap,
                "source_doc": ev.source_doc.to_dict() if ev.source_doc else None,
                "strategy_bucket": self._fade_bucket,
            }

            # Gate (2.5b brain). LLMSpendCapExceeded propagates — hard rule:
            # never improvise around a spend cap.
            stated_probability = self._cfg.fade_stated_probability
            gate_prompt_version: str | None = None
            fade_rationale = ""
            if self._fade_gate is not None:
                assessment = self._fade_gate(ev)
                gate_prompt_version = getattr(assessment, "prompt_version", None)
                gate_inputs = {
                    "scored": bool(getattr(assessment, "scored", False)),
                    "classification": getattr(assessment, "classification", None),
                    "stated_probability": getattr(assessment, "stated_probability", None),
                    "ensemble_probability": getattr(assessment, "ensemble_probability", None),
                    "hard_skip": bool(getattr(assessment, "hard_skip", False)),
                    "hard_skip_reasons": list(getattr(assessment, "hard_skip_reasons", []) or []),
                    "forced_flow": bool(getattr(assessment, "forced_flow", False)),
                    "vetoed": bool(getattr(assessment, "vetoed", False)),
                    # Hard rule 3: the counter-pass result is logged on EVERY
                    # fade decision, entered or vetoed.
                    "counter_pass": dict(getattr(assessment, "counter_pass", {}) or {}),
                    "flags": list(getattr(assessment, "flags", []) or []),
                    "model_probabilities": dict(
                        getattr(assessment, "model_probabilities", {}) or {}
                    ),
                    "model_classifications": dict(
                        getattr(assessment, "model_classifications", {}) or {}
                    ),
                }
                if assessment is None or not getattr(assessment, "passes_entry_threshold", False):
                    action = (
                        "fade_vetoed"
                        if getattr(assessment, "vetoed", False)
                        else "skipped_fade_gate"
                    )
                    ctx.store.record_decision(
                        decision_id=str(uuid.uuid4()),
                        bot_id=self._bot_id,
                        decision_type="entry",
                        action=action,
                        inputs={**crash_inputs, "fade_gate": gate_inputs},
                        mode=ctx.mode,
                        as_of=ctx.now,
                        run_id=getattr(self._run_context, "run_id", None),
                        prompt_version=gate_prompt_version,
                    )
                    continue
                stated_probability = float(assessment.stated_probability)
                crash_inputs["fade_gate"] = gate_inputs
                flags = list(getattr(assessment, "flags", []) or [])
                flag_note = f" [{','.join(flags)}]" if flags else ""
                fade_rationale = f" | rubric: {assessment.rationale}{flag_note}"

            try:
                quote = ctx.datafeed.get_quote(ev.symbol)
            except ValueError:
                continue
            if quote.price <= 0:
                continue
            ctx.set_mark(ev.symbol, quote.price)
            equity = ctx.broker.get_portfolio(ctx.now).total_equity
            quantity = round((equity * self._cfg.fade_position_pct) / quote.price, 4)
            if quantity <= 0:
                continue

            thesis_id = str(uuid.uuid4())
            ledger.record_prediction(
                thesis_id=thesis_id,
                bot_id=self._bot_id,
                symbol=ev.symbol,
                direction="long",
                stated_probability=stated_probability,
                trade_type=FADE_TRADE_TYPE,
                mode=ctx.mode,
                rationale=(
                    f"Fade entry: crash {ev.drop_pct:+.1%} on {ev.as_of.date().isoformat()}"
                    f"{fade_rationale}"
                ),
                strategy_bucket=self._fade_bucket,
            )
            order = OrderRequest(
                symbol=ev.symbol,
                side=Side.BUY,
                quantity=quantity,
                order_type=OrderType.MARKET,
                bot_id=self._bot_id,
                thesis_id=thesis_id,
            )
            result = ctx.submit_and_record(order)
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="entry",
                action="fade_buy" if result.status != OrderStatus.REJECTED else "rejected",
                inputs={
                    **crash_inputs,
                    "quote_price": quote.price,
                    "quantity": quantity,
                    "equity": equity,
                    "order_status": result.status.value,
                    "stated_probability": stated_probability,
                },
                mode=ctx.mode,
                as_of=ctx.now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=thesis_id,
                prompt_version=gate_prompt_version,
            )
            if result.status == OrderStatus.REJECTED:
                continue
            self._open[ev.symbol] = _OpenTrade(
                symbol=ev.symbol,
                thesis_id=thesis_id,
                entry_order_id=result.order_id,
                entry_ts=ctx.now,
                event_id=ev.event_id,
                stated_probability=stated_probability,
                trade_kind="fade",
            )

    def _process_exits(self, ctx: StrategyContext) -> None:
        now = ctx.now
        for symbol, trade in list(self._open.items()):
            if trade.trade_kind == "fade":
                self._process_fade_exit(ctx, symbol, trade)
                continue
            if self._cfg.exit_rules_enabled:
                self._process_exit_rules(ctx, symbol, trade)
                continue
            # 2b default path: pure time-stop after hold_days (unchanged).
            if now < trade.entry_ts + timedelta(days=self._cfg.hold_days):
                continue
            pos = self._position_or_handle_missing(ctx, symbol, trade)
            if pos is None:
                continue
            try:
                quote = ctx.datafeed.get_quote(symbol)
                ctx.set_mark(symbol, quote.price)
            except ValueError:
                pass  # sell at the last known mark
            self._submit_full_exit(
                ctx, symbol, trade, pos.quantity, action="sell",
                extra_inputs={"hold_days": self._cfg.hold_days},
            )

    # --- 2d rule-based exits ------------------------------------------------

    def _process_exit_rules(self, ctx: StrategyContext, symbol: str, trade: _OpenTrade) -> None:
        """Profit-take 50% at +15%; 7% trailing stop; -8% hard stop; 14d time-stop."""
        now = ctx.now
        if trade.entry_price is None:
            # Rules need a settled entry. Wait a bounded number of ticks for the
            # next-bar fill, then abandon (e.g. the entry was rejected at settle).
            entry_fill = self._fill_for_order(ctx, trade.entry_order_id)
            if entry_fill is None:
                trade.missing_position_ticks += 1
                if trade.missing_position_ticks >= MISSING_POSITION_RETRY_TICKS:
                    ctx.store.record_decision(
                        decision_id=str(uuid.uuid4()),
                        bot_id=self._bot_id,
                        decision_type="exit",
                        action="abandoned_no_entry_fill",
                        inputs={"symbol": symbol, "thesis_id": trade.thesis_id},
                        mode=ctx.mode,
                        as_of=now,
                        run_id=getattr(self._run_context, "run_id", None),
                        thesis_id=trade.thesis_id,
                    )
                    self._open.pop(symbol, None)
                return
            trade.entry_price = float(entry_fill["price"])
            trade.high_water = trade.entry_price
            trade.missing_position_ticks = 0
        pos = self._position_or_handle_missing(ctx, symbol, trade)
        if pos is None:
            return
        try:
            quote = ctx.datafeed.get_quote(symbol)
            ctx.set_mark(symbol, quote.price)
            price = quote.price
        except ValueError:
            return  # no quote this tick: evaluate rules next tick
        if price <= 0:
            return
        trade.high_water = max(trade.high_water or price, price)
        ret_from_entry = (price - trade.entry_price) / trade.entry_price
        off_high = (price - trade.high_water) / trade.high_water if trade.high_water else 0.0
        rule_inputs = {
            "price": price,
            "entry_price": trade.entry_price,
            "high_water": trade.high_water,
            "return_from_entry": ret_from_entry,
            "off_high": off_high,
        }

        if ret_from_entry <= -self._cfg.hard_stop_pct:
            self._submit_full_exit(ctx, symbol, trade, pos.quantity, action="hard_stop",
                                   extra_inputs=rule_inputs)
            return
        if off_high <= -self._cfg.trailing_stop_pct:
            self._submit_full_exit(ctx, symbol, trade, pos.quantity, action="trailing_stop",
                                   extra_inputs=rule_inputs)
            return
        if now >= trade.entry_ts + timedelta(days=self._cfg.time_stop_days):
            self._submit_full_exit(ctx, symbol, trade, pos.quantity, action="time_stop",
                                   extra_inputs=rule_inputs)
            return
        if not trade.profit_taken and ret_from_entry >= self._cfg.profit_take_trigger_pct:
            quantity = round(pos.quantity * self._cfg.profit_take_fraction, 4)
            if quantity <= 0 or quantity >= pos.quantity:
                return
            sell = OrderRequest(
                symbol=symbol, side=Side.SELL, quantity=quantity,
                order_type=OrderType.MARKET, bot_id=self._bot_id, thesis_id=trade.thesis_id,
            )
            result = ctx.submit_and_record(sell)
            if result.status == OrderStatus.REJECTED:
                return  # retry next tick
            trade.profit_taken = True
            trade.exit_order_ids.append(result.order_id)
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="exit",
                action="profit_take_partial",
                inputs={
                    "symbol": symbol, "thesis_id": trade.thesis_id,
                    "quantity": quantity, "order_status": result.status.value,
                    **rule_inputs,
                },
                mode=ctx.mode,
                as_of=now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=trade.thesis_id,
            )

    # --- 2.5c fade exits ------------------------------------------------------

    def _process_fade_exit(self, ctx: StrategyContext, symbol: str, trade: _OpenTrade) -> None:
        """Pre-registered fade exits: -8% hard stop (immediate), 10-day time-stop.

        The 2-day minimum hold applies to any non-stop exit; protective stops
        are exempt by design (a stop that waits is not a stop).
        """
        now = ctx.now
        if trade.entry_price is None:
            entry_fill = self._fill_for_order(ctx, trade.entry_order_id)
            if entry_fill is None:
                trade.missing_position_ticks += 1
                if trade.missing_position_ticks >= MISSING_POSITION_RETRY_TICKS:
                    ctx.store.record_decision(
                        decision_id=str(uuid.uuid4()),
                        bot_id=self._bot_id,
                        decision_type="exit",
                        action="abandoned_no_entry_fill",
                        inputs={"symbol": symbol, "thesis_id": trade.thesis_id,
                                "trade_kind": "fade"},
                        mode=ctx.mode,
                        as_of=now,
                        run_id=getattr(self._run_context, "run_id", None),
                        thesis_id=trade.thesis_id,
                    )
                    self._open.pop(symbol, None)
                return
            trade.entry_price = float(entry_fill["price"])
            trade.missing_position_ticks = 0
        pos = self._position_or_handle_missing(ctx, symbol, trade)
        if pos is None:
            return
        try:
            quote = ctx.datafeed.get_quote(symbol)
            ctx.set_mark(symbol, quote.price)
            price = quote.price
        except ValueError:
            return  # no quote this tick: evaluate next tick
        if price <= 0:
            return
        ret_from_entry = (price - trade.entry_price) / trade.entry_price
        rule_inputs = {
            "price": price,
            "entry_price": trade.entry_price,
            "return_from_entry": ret_from_entry,
            "trade_kind": "fade",
        }
        if ret_from_entry <= -self._cfg.fade_hard_stop_pct:
            self._submit_full_exit(ctx, symbol, trade, pos.quantity,
                                   action="fade_hard_stop", extra_inputs=rule_inputs)
            return
        if now >= trade.entry_ts + timedelta(days=self._cfg.fade_time_stop_days):
            self._submit_full_exit(ctx, symbol, trade, pos.quantity,
                                   action="fade_time_stop", extra_inputs=rule_inputs)

    # --- shared exit plumbing ------------------------------------------------

    def _position_or_handle_missing(self, ctx: StrategyContext, symbol: str, trade: _OpenTrade):
        """Return the live position, or run the 2-R1 missing-position protocol."""
        now = ctx.now
        snapshot = ctx.broker.get_portfolio(now)
        pos = next((p for p in snapshot.positions if p.symbol == symbol), None)
        if pos is not None and pos.quantity > 0:
            trade.missing_position_ticks = 0
            return pos
        entry_fill = self._fill_for_order(ctx, trade.entry_order_id)
        if entry_fill is None:
            # Entry never settled (e.g. next-bar order rejected): abandon.
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="exit",
                action="abandoned_no_entry_fill",
                inputs={"symbol": symbol, "thesis_id": trade.thesis_id},
                mode=ctx.mode,
                as_of=now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=trade.thesis_id,
            )
            self._open.pop(symbol, None)
            return None
        # 2-R1: entry FILLED but the position is gone (e.g. broker
        # reconciliation surprise in live mode). Retry a bounded number
        # of ticks, then abandon LOUDLY in the audit trail instead of
        # retrying silently forever.
        trade.missing_position_ticks += 1
        if trade.missing_position_ticks >= MISSING_POSITION_RETRY_TICKS:
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="exit",
                action="abandoned_position_missing",
                inputs={
                    "symbol": symbol,
                    "thesis_id": trade.thesis_id,
                    "entry_order_id": trade.entry_order_id,
                    "entry_fill_id": entry_fill["fill_id"],
                    "retries": trade.missing_position_ticks,
                },
                mode=ctx.mode,
                as_of=now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=trade.thesis_id,
            )
            if self._run_context is not None:
                self._run_context.info(
                    "trade abandoned: entry filled but position missing",
                    extra_fields={
                        "bot_id": self._bot_id,
                        "symbol": symbol,
                        "thesis_id": trade.thesis_id,
                        "retries": trade.missing_position_ticks,
                    },
                )
            self._open.pop(symbol, None)
        return None

    def _submit_full_exit(
        self,
        ctx: StrategyContext,
        symbol: str,
        trade: _OpenTrade,
        quantity: float,
        *,
        action: str,
        extra_inputs: dict[str, Any] | None = None,
    ) -> None:
        """Sell the whole remaining position and move the trade to closing."""
        sell = OrderRequest(
            symbol=symbol,
            side=Side.SELL,
            quantity=quantity,
            order_type=OrderType.MARKET,
            bot_id=self._bot_id,
            thesis_id=trade.thesis_id,
        )
        result = ctx.submit_and_record(sell)
        if result.status == OrderStatus.REJECTED:
            return  # retry next tick
        ctx.store.record_decision(
            decision_id=str(uuid.uuid4()),
            bot_id=self._bot_id,
            decision_type="exit",
            action=action,
            inputs={
                "symbol": symbol,
                "thesis_id": trade.thesis_id,
                "entry_ts": trade.entry_ts.isoformat(),
                "quantity": quantity,
                "order_status": result.status.value,
                **(extra_inputs or {}),
            },
            mode=ctx.mode,
            as_of=ctx.now,
            run_id=getattr(self._run_context, "run_id", None),
            thesis_id=trade.thesis_id,
        )
        self._closing.append(
            _ClosingTrade(
                symbol=symbol,
                thesis_id=trade.thesis_id,
                entry_order_id=trade.entry_order_id,
                exit_order_ids=[*trade.exit_order_ids, result.order_id],
                event_id=trade.event_id,
                stated_probability=trade.stated_probability,
                trade_kind=trade.trade_kind,
            )
        )
        self._open.pop(symbol, None)

    # --- 2d: state round-trip for the daily one-tick paper process -----------

    def export_state(self) -> dict[str, Any]:
        return {
            "surprise_history": list(self._surprise_history),
            "open": [
                {
                    "symbol": t.symbol,
                    "thesis_id": t.thesis_id,
                    "entry_order_id": t.entry_order_id,
                    "entry_ts": t.entry_ts.isoformat(),
                    "event_id": t.event_id,
                    "missing_position_ticks": t.missing_position_ticks,
                    "stated_probability": t.stated_probability,
                    "trade_kind": t.trade_kind,
                    "entry_price": t.entry_price,
                    "high_water": t.high_water,
                    "profit_taken": t.profit_taken,
                    "exit_order_ids": list(t.exit_order_ids),
                }
                for t in self._open.values()
            ],
            "closing": [
                {
                    "symbol": t.symbol,
                    "thesis_id": t.thesis_id,
                    "entry_order_id": t.entry_order_id,
                    "exit_order_ids": list(t.exit_order_ids),
                    "event_id": t.event_id,
                    "stated_probability": t.stated_probability,
                    "trade_kind": t.trade_kind,
                }
                for t in self._closing
            ],
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        self._surprise_history = [float(x) for x in state.get("surprise_history", [])]
        self._open = {
            raw["symbol"]: _OpenTrade(
                symbol=raw["symbol"],
                thesis_id=raw["thesis_id"],
                entry_order_id=raw["entry_order_id"],
                entry_ts=datetime.fromisoformat(raw["entry_ts"]),
                event_id=raw["event_id"],
                missing_position_ticks=int(raw.get("missing_position_ticks", 0)),
                stated_probability=float(raw.get("stated_probability", 0.55)),
                trade_kind=str(raw.get("trade_kind", "ride")),
                entry_price=raw.get("entry_price"),
                high_water=raw.get("high_water"),
                profit_taken=bool(raw.get("profit_taken", False)),
                exit_order_ids=list(raw.get("exit_order_ids", [])),
            )
            for raw in state.get("open", [])
        }
        self._closing = [
            _ClosingTrade(
                symbol=raw["symbol"],
                thesis_id=raw["thesis_id"],
                entry_order_id=raw["entry_order_id"],
                exit_order_ids=list(raw.get("exit_order_ids", [])),
                event_id=raw["event_id"],
                stated_probability=float(raw.get("stated_probability", 0.55)),
                trade_kind=str(raw.get("trade_kind", "ride")),
            )
            for raw in state.get("closing", [])
        ]

    def _fill_for_order(self, ctx: StrategyContext, order_id: str) -> dict[str, Any] | None:
        fills = ctx.store.get_fills(self._bot_id, ctx.mode)
        return next((f for f in fills if f["order_id"] == order_id), None)

    def _resolve_closed_trades(self, ctx: StrategyContext) -> None:
        """Resolve theses whose round trip has fully settled (both fills persisted)."""
        if not self._closing:
            return
        fills = ctx.store.get_fills(self._bot_id, ctx.mode)
        by_order = {f["order_id"]: f for f in fills}
        ledger = CalibrationLedger(ctx.store)
        still_closing: list[_ClosingTrade] = []
        for trade in self._closing:
            entry = by_order.get(trade.entry_order_id)
            exits = [by_order.get(oid) for oid in trade.exit_order_ids]
            if entry is None or any(e is None for e in exits) or not exits:
                still_closing.append(trade)
                continue
            # Quantity-weighted average exit price across partial exits (2d).
            total_qty = sum(e["quantity"] for e in exits)
            avg_exit = sum(e["price"] * e["quantity"] for e in exits) / total_qty
            net_return = (avg_exit - entry["price"]) / entry["price"]
            outcome = net_return > 0
            is_fade = trade.trade_kind == "fade"
            bucket = self._fade_bucket if is_fade else self._bucket
            ledger.resolve(
                thesis_id=trade.thesis_id,
                bot_id=self._bot_id,
                trade_type=FADE_TRADE_TYPE if is_fade else TRADE_TYPE,
                stated_probability=trade.stated_probability,
                outcome=outcome,
                bucket=ledger.bucket_for_probability(trade.stated_probability),
                resolved_at=ctx.now,
                strategy_bucket=bucket,
            )
            ctx.store.record_decision(
                decision_id=str(uuid.uuid4()),
                bot_id=self._bot_id,
                decision_type="resolution",
                action="win" if outcome else "loss",
                inputs={
                    "symbol": trade.symbol,
                    "thesis_id": trade.thesis_id,
                    "event_id": trade.event_id,
                    "entry_price": entry["price"],
                    "exit_price": avg_exit,
                    "exit_fills": len(exits),
                    "net_return": net_return,
                    "strategy_bucket": bucket,
                    "trade_kind": trade.trade_kind,
                },
                mode=ctx.mode,
                as_of=ctx.now,
                run_id=getattr(self._run_context, "run_id", None),
                thesis_id=trade.thesis_id,
            )
        self._closing = still_closing

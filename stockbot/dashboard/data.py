"""Dashboard data layer (Phase 4b / 4b-2) — read-only queries against the state DB.

Pure functions over SQLite so the rendering layer (and tests) need no broker,
no harness, and no live process: everything comes from persisted state.
Zero write paths: connections are opened in SQLite read-only mode.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Match app.py DISPLAY_TZ — used only to decide whether a run counts as "today".
_DISPLAY_TZ = ZoneInfo("America/Chicago")

CALIBRATION_MIN_SAMPLES = 10
CALIBRATION_MAX_MEAN_BRIER = 0.25


def db_exists(db_path: str | Path) -> bool:
    return Path(db_path).is_file()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass(frozen=True)
class BotLane:
    bot_id: str
    mode: str
    snapshots: int


def list_bot_lanes(db_path: str | Path) -> list[BotLane]:
    """Every (bot_id, mode) pair that has persisted snapshots."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT bot_id, mode, COUNT(*) AS n
            FROM portfolio_snapshots
            GROUP BY bot_id, mode
            ORDER BY bot_id, mode
            """
        ).fetchall()
    return [BotLane(bot_id=r["bot_id"], mode=r["mode"], snapshots=r["n"]) for r in rows]


def equity_curve(db_path: str | Path, bot_id: str, mode: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT as_of, cash, total_equity
            FROM portfolio_snapshots
            WHERE bot_id = ? AND mode = ?
            ORDER BY as_of ASC, id ASC
            """,
            (bot_id, mode),
        ).fetchall()
    return [
        {"as_of": r["as_of"], "cash": r["cash"], "total_equity": r["total_equity"]} for r in rows
    ]


def calibration_by_strategy_bucket(db_path: str | Path) -> list[dict[str, Any]]:
    """Per (bot_id, strategy_bucket): sample count, mean Brier, hit rate, capital gate."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT bot_id, strategy_bucket,
                   COUNT(*) AS n,
                   AVG(brier_score) AS mean_brier,
                   AVG(outcome) AS hit_rate
            FROM calibration_ledger
            GROUP BY bot_id, strategy_bucket
            ORDER BY bot_id, strategy_bucket
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        n = int(r["n"])
        mean_brier = float(r["mean_brier"]) if r["mean_brier"] is not None else None
        out.append(
            {
                "bot_id": r["bot_id"],
                "strategy_bucket": r["strategy_bucket"] or "(untagged)",
                "count": n,
                "mean_brier": mean_brier,
                "hit_rate": float(r["hit_rate"]) if r["hit_rate"] is not None else None,
                "calibrated": (
                    n >= CALIBRATION_MIN_SAMPLES
                    and mean_brier is not None
                    and mean_brier <= CALIBRATION_MAX_MEAN_BRIER
                ),
            }
        )
    return out


def slippage_summary(
    db_path: str | Path,
    assumed_slippage_pct: float | None = None,
) -> list[dict[str, Any]]:
    """Realized slippage per (bot_id, mode) from persisted fills, vs the assumption."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT bot_id, mode,
                   COUNT(*) AS fills,
                   AVG(slippage_pct) AS mean_slippage_pct,
                   MAX(slippage_pct) AS max_slippage_pct,
                   SUM(quantity * price) AS traded_notional
            FROM fills
            GROUP BY bot_id, mode
            ORDER BY bot_id, mode
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        mean_realized = float(r["mean_slippage_pct"]) if r["mean_slippage_pct"] is not None else None
        entry: dict[str, Any] = {
            "bot_id": r["bot_id"],
            "mode": r["mode"],
            "fills": int(r["fills"]),
            "mean_realized_slippage_pct": mean_realized,
            "max_realized_slippage_pct": (
                float(r["max_slippage_pct"]) if r["max_slippage_pct"] is not None else None
            ),
            "traded_notional": float(r["traded_notional"]) if r["traded_notional"] is not None else 0.0,
            "assumed_slippage_pct": assumed_slippage_pct,
            "realized_vs_assumed": None,
        }
        if assumed_slippage_pct and mean_realized is not None and assumed_slippage_pct > 0:
            entry["realized_vs_assumed"] = mean_realized / assumed_slippage_pct
        out.append(entry)
    return out


def live_slippage_detail(
    db_path: str | Path,
    *,
    mode: str = "live",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Per-fill expected-vs-realized for live fills (shot 12).

    The reconciler stores the SIGNED slippage_pct on each live fill (positive =
    adverse/cost; BUY pays above expected, SELL receives below). The expected
    (decision-time) price is recovered from the realized price + signed slippage
    + side, so the panel shows expected vs realized per fill without a second
    column on the fills table:
      BUY : expected = realized / (1 + slippage)
      SELL: expected = realized / (1 - slippage)
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT fill_id, order_id, symbol, side, quantity, price, slippage_pct,
                   bot_id, mode, filled_at, thesis_id
            FROM fills
            WHERE mode = ?
            ORDER BY filled_at DESC, fill_id ASC
            LIMIT ?
            """,
            (mode, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        side = r["side"]
        realized = float(r["price"])
        slip = float(r["slippage_pct"])
        denom = (1.0 + slip) if side == "buy" else (1.0 - slip)
        expected = realized / denom if denom else None
        out.append(
            {
                "fill_id": r["fill_id"],
                "order_id": r["order_id"],
                "symbol": r["symbol"],
                "side": side,
                "quantity": float(r["quantity"]),
                "expected_price": expected,
                "realized_price": realized,
                "slippage_pct": slip,
                "bot_id": r["bot_id"],
                "filled_at": r["filled_at"],
                "thesis_id": r["thesis_id"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# 4b-2 panel 2 — open positions with exit-rule state
# ---------------------------------------------------------------------------

# Exit-rule parameters (2d, fixed in the plan). Kept as module constants so the
# dashboard needs no import of strategy code; values mirror RideConfig defaults.
EXIT_PROFIT_TAKE_TRIGGER_PCT = 0.15
EXIT_TRAILING_STOP_PCT = 0.07
EXIT_HARD_STOP_PCT = 0.08
EXIT_TIME_STOP_DAYS = 14


def open_positions(
    db_path: str | Path,
    bot_id: str,
    mode: str,
    sidecar_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Positions from the lane's latest snapshot, merged with the strategy
    sidecar's open-trade state (entry price / high-water / profit-taken) to
    compute distance to each 2d exit rule. Read-only.
    """
    with _connect(db_path) as conn:
        snap = conn.execute(
            """
            SELECT id, as_of, cash, total_equity
            FROM portfolio_snapshots
            WHERE bot_id = ? AND mode = ?
            ORDER BY as_of DESC, id DESC
            LIMIT 1
            """,
            (bot_id, mode),
        ).fetchone()
        if snap is None:
            return {"bot_id": bot_id, "as_of": None, "cash": None, "total_equity": None, "positions": []}
        pos_rows = conn.execute(
            """
            SELECT symbol, quantity, avg_cost, market_value, unrealized_pnl
            FROM positions WHERE snapshot_id = ?
            ORDER BY symbol ASC
            """,
            (snap["id"],),
        ).fetchall()

    trades_by_symbol = {t.get("symbol"): t for t in (sidecar_trades or [])}
    as_of = datetime.fromisoformat(snap["as_of"])
    positions: list[dict[str, Any]] = []
    for r in pos_rows:
        qty = float(r["quantity"])
        mark = float(r["market_value"]) / qty if qty else None
        trade = trades_by_symbol.get(r["symbol"], {})
        entry_price = trade.get("entry_price") or float(r["avg_cost"])
        high_water = trade.get("high_water") or entry_price
        entry_ts_raw = trade.get("entry_ts")
        entry_ts = datetime.fromisoformat(entry_ts_raw) if entry_ts_raw else None

        item: dict[str, Any] = {
            "symbol": r["symbol"],
            "quantity": qty,
            "avg_cost": float(r["avg_cost"]),
            "mark": mark,
            "market_value": float(r["market_value"]),
            "unrealized_pnl": float(r["unrealized_pnl"]),
            "return_from_entry": None,
            "thesis_id": trade.get("thesis_id"),
            "entry_ts": entry_ts_raw,
            "profit_taken": bool(trade.get("profit_taken", False)),
            "stated_probability": trade.get("stated_probability"),
            # exit-rule state (None when not computable yet):
            "hard_stop_price": None,
            "to_hard_stop_pct": None,
            "trail_stop_price": None,
            "to_trail_stop_pct": None,
            "to_profit_take_pct": None,
            "time_stop_at": None,
            "days_to_time_stop": None,
        }
        if entry_price and mark:
            ret = (mark - entry_price) / entry_price
            item["return_from_entry"] = ret
            hard = entry_price * (1.0 - EXIT_HARD_STOP_PCT)
            trail = high_water * (1.0 - EXIT_TRAILING_STOP_PCT)
            item["hard_stop_price"] = hard
            item["to_hard_stop_pct"] = (mark - hard) / mark
            item["trail_stop_price"] = trail
            item["to_trail_stop_pct"] = (mark - trail) / mark
            if not item["profit_taken"]:
                item["to_profit_take_pct"] = EXIT_PROFIT_TAKE_TRIGGER_PCT - ret
        if entry_ts is not None:
            stop_at = entry_ts + timedelta(days=EXIT_TIME_STOP_DAYS)
            item["time_stop_at"] = stop_at.isoformat()
            item["days_to_time_stop"] = (stop_at - as_of).total_seconds() / 86400.0
        positions.append(item)
    return {
        "bot_id": bot_id,
        "as_of": snap["as_of"],
        "cash": float(snap["cash"]),
        "total_equity": float(snap["total_equity"]),
        "positions": positions,
    }


# ---------------------------------------------------------------------------
# 4b-2 panel 3 — thesis browser
# ---------------------------------------------------------------------------


def _entry_quality(inputs: dict[str, Any]) -> dict[str, Any]:
    quality = inputs.get("quality") or {}
    return {
        "quality_score": quality.get("quality_score"),
        "flags": list(quality.get("flags", []) or []),
        "triage": quality.get("triage") or {},
    }


def list_theses(
    db_path: str | Path,
    *,
    mode: str | None = None,
    bot_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Theses (newest first) joined to their entry decision for quality/flags."""
    where = ["1=1"]
    params: list[Any] = []
    if mode is not None:
        where.append("t.mode = ?")
        params.append(mode)
    if bot_id is not None:
        where.append("t.bot_id = ?")
        params.append(bot_id)
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT t.thesis_id, t.bot_id, t.symbol, t.direction, t.stated_probability,
                   t.rationale, t.mode, t.strategy_bucket, t.created_at, t.resolved_at, t.outcome,
                   d.inputs_json AS entry_inputs_json, d.model_id, d.prompt_version
            FROM theses t
            LEFT JOIN decisions d
              ON d.thesis_id = t.thesis_id AND d.decision_type = 'entry'
            WHERE {' AND '.join(where)}
            ORDER BY t.created_at DESC, t.thesis_id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        entry_inputs = json.loads(r["entry_inputs_json"]) if r["entry_inputs_json"] else {}
        q = _entry_quality(entry_inputs)
        out.append(
            {
                "thesis_id": r["thesis_id"],
                "bot_id": r["bot_id"],
                "symbol": r["symbol"],
                "direction": r["direction"],
                "stated_probability": r["stated_probability"],
                "rationale": r["rationale"] or "",
                "mode": r["mode"],
                "strategy_bucket": r["strategy_bucket"],
                "created_at": r["created_at"],
                "resolved_at": r["resolved_at"],
                "outcome": r["outcome"],
                "quality_score": q["quality_score"],
                "flags": q["flags"],
                "prompt_version": r["prompt_version"],
                "surprise_pct": entry_inputs.get("surprise_pct"),
                "actual_eps": entry_inputs.get("actual_eps"),
                "consensus_eps": entry_inputs.get("consensus_eps"),
            }
        )
    return out


def ensemble_health(
    db_path: str | Path,
    *,
    bot_id: str,
    mode: str = "paper",
    lookback: int = 25,
) -> dict[str, Any]:
    """AI-ensemble health for the gated lane: are the most recent theses flagged
    as missing the GPT cross-check? Read-only; derived from flags already stored.

    'Most recent' = every thesis sharing the newest created_at (i.e. the latest
    paper tick). ``degraded`` is True if any of those carry an
    ``ensemble_unavailable`` / ``cross_model_failed`` flag.
    """
    degraded_terms = {"ensemble_unavailable", "cross_model_failed"}
    theses = list_theses(db_path, mode=mode, bot_id=bot_id, limit=lookback)
    if not theses:
        return {"checked": 0, "flagged": 0, "degraded": False, "as_of": None}
    latest_created = theses[0]["created_at"]
    latest = [t for t in theses if t["created_at"] == latest_created]
    flagged = [
        t
        for t in latest
        if any(f.split(":", 1)[0] in degraded_terms for f in (t["flags"] or []))
    ]
    return {
        "checked": len(latest),
        "flagged": len(flagged),
        "degraded": bool(flagged),
        "as_of": latest_created,
    }


def thesis_detail(db_path: str | Path, thesis_id: str) -> dict[str, Any] | None:
    """Full audit view of one thesis: row + every decision + matched fills.

    Fills are matched by the direct fills.thesis_id join (2-R3). Legacy fills
    written before the migration have thesis_id NULL, so when the direct join
    finds nothing we fall back to the old (bot, mode, symbol) trade-window
    match — exact for this strategy family (≤1 open trade per symbol per lane).
    """
    with _connect(db_path) as conn:
        t = conn.execute("SELECT * FROM theses WHERE thesis_id = ?", (thesis_id,)).fetchone()
        if t is None:
            return None
        d_rows = conn.execute(
            """
            SELECT decision_id, run_id, decision_type, action, inputs_json,
                   model_id, prompt_version, mode, as_of
            FROM decisions
            WHERE thesis_id = ?
            ORDER BY as_of ASC, decision_id ASC
            """,
            (thesis_id,),
        ).fetchall()
        f_rows = conn.execute(
            """
            SELECT fill_id, order_id, side, quantity, price, slippage_pct, filled_at
            FROM fills
            WHERE thesis_id = ?
            ORDER BY filled_at ASC
            """,
            (thesis_id,),
        ).fetchall()
        if not f_rows:
            # Legacy fallback: pre-migration fills (thesis_id NULL) — window match.
            window_end = t["resolved_at"]
            params: list[Any] = [t["bot_id"], t["mode"], t["symbol"], t["created_at"]]
            end_clause = ""
            if window_end:
                # next-bar settles persist before resolution; small slack for safety
                end_dt = datetime.fromisoformat(window_end) + timedelta(days=3)
                end_clause = " AND filled_at <= ?"
                params.append(end_dt.isoformat())
            f_rows = conn.execute(
                f"""
                SELECT fill_id, order_id, side, quantity, price, slippage_pct, filled_at
                FROM fills
                WHERE bot_id = ? AND mode = ? AND symbol = ? AND filled_at >= ?{end_clause}
                  AND thesis_id IS NULL
                ORDER BY filled_at ASC
                """,
                tuple(params),
            ).fetchall()
    decisions = [
        {
            "decision_id": r["decision_id"],
            "run_id": r["run_id"],
            "decision_type": r["decision_type"],
            "action": r["action"],
            "inputs": json.loads(r["inputs_json"]),
            "model_id": r["model_id"],
            "prompt_version": r["prompt_version"],
            "as_of": r["as_of"],
        }
        for r in d_rows
    ]
    entry_inputs = next(
        (d["inputs"] for d in decisions if d["decision_type"] == "entry"), {}
    )
    return {
        "thesis_id": t["thesis_id"],
        "bot_id": t["bot_id"],
        "symbol": t["symbol"],
        "direction": t["direction"],
        "stated_probability": t["stated_probability"],
        "rationale": t["rationale"] or "",
        "mode": t["mode"],
        "strategy_bucket": t["strategy_bucket"],
        "created_at": t["created_at"],
        "resolved_at": t["resolved_at"],
        "outcome": t["outcome"],
        "quality": entry_inputs.get("quality") or {},
        "decisions": decisions,
        "fills": [
            {
                "fill_id": r["fill_id"],
                "order_id": r["order_id"],
                "side": r["side"],
                "quantity": r["quantity"],
                "price": r["price"],
                "slippage_pct": r["slippage_pct"],
                "filled_at": r["filled_at"],
            }
            for r in f_rows
        ],
    }


def thesis_id_for_fill(db_path: str | Path, fill_id: str) -> str | None:
    """Drill-down from a fill: the direct fills.thesis_id column (2-R3), with
    the legacy window match as fallback for pre-migration NULL rows."""
    with _connect(db_path) as conn:
        f = conn.execute("SELECT * FROM fills WHERE fill_id = ?", (fill_id,)).fetchone()
        if f is None:
            return None
        if f["thesis_id"]:
            return f["thesis_id"]
        row = conn.execute(
            """
            SELECT thesis_id FROM theses
            WHERE bot_id = ? AND mode = ? AND symbol = ? AND created_at <= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (f["bot_id"], f["mode"], f["symbol"], f["filled_at"]),
        ).fetchone()
    return row["thesis_id"] if row else None


def recent_fills(
    db_path: str | Path, *, mode: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Newest fills with their resolved thesis link (for drill-down)."""
    where, params = "1=1", []
    if mode is not None:
        where, params = "mode = ?", [mode]
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT fill_id, order_id, symbol, side, quantity, price, slippage_pct,
                   bot_id, mode, filled_at
            FROM fills
            WHERE {where}
            ORDER BY filled_at DESC, fill_id ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "fill_id": r["fill_id"],
                "order_id": r["order_id"],
                "symbol": r["symbol"],
                "side": r["side"],
                "quantity": r["quantity"],
                "price": r["price"],
                "slippage_pct": r["slippage_pct"],
                "bot_id": r["bot_id"],
                "mode": r["mode"],
                "filled_at": r["filled_at"],
                "thesis_id": thesis_id_for_fill(db_path, r["fill_id"]),
            }
        )
    return out


def gate_skips(
    db_path: str | Path, *, mode: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Entries the 2c quality gate rejected (action='skipped_quality_gate')."""
    where = ["decision_type = 'entry'", "action = 'skipped_quality_gate'"]
    params: list[Any] = []
    if mode is not None:
        where.append("mode = ?")
        params.append(mode)
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT decision_id, bot_id, inputs_json, prompt_version, as_of
            FROM decisions
            WHERE {' AND '.join(where)}
            ORDER BY as_of DESC, decision_id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    out = []
    for r in rows:
        inputs = json.loads(r["inputs_json"])
        q = _entry_quality(inputs)
        out.append(
            {
                "decision_id": r["decision_id"],
                "bot_id": r["bot_id"],
                "as_of": r["as_of"],
                "symbol": inputs.get("symbol"),
                "event_id": inputs.get("event_id"),
                "surprise_pct": inputs.get("surprise_pct"),
                "quality_score": q["quality_score"],
                "stated_probability": (inputs.get("quality") or {}).get("stated_probability"),
                "flags": q["flags"],
                "triage_reason": q["triage"].get("reason", ""),
                "prompt_version": r["prompt_version"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# 4b-2 panel 5 — calibration curves (stated probability vs realized hit rate)
# ---------------------------------------------------------------------------

CALIBRATION_BIN_EDGES = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0001]


def calibration_curves(db_path: str | Path) -> list[dict[str, Any]]:
    """Per (bot_id, strategy_bucket): binned stated probability vs realized hit
    rate — the curve behind the gate flag, not just the flag."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT bot_id, strategy_bucket, stated_probability, outcome
            FROM calibration_ledger
            ORDER BY bot_id, strategy_bucket
            """
        ).fetchall()
    grouped: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for r in rows:
        key = (r["bot_id"], r["strategy_bucket"] or "(untagged)")
        grouped.setdefault(key, []).append((float(r["stated_probability"]), int(r["outcome"])))
    out: list[dict[str, Any]] = []
    for (bot, bucket), samples in grouped.items():
        bins = []
        for lo, hi in zip(CALIBRATION_BIN_EDGES, CALIBRATION_BIN_EDGES[1:]):
            members = [(p, o) for p, o in samples if lo <= p < hi]
            if not members:
                continue
            bins.append(
                {
                    "bin_low": lo,
                    "bin_high": min(hi, 1.0),
                    "count": len(members),
                    "mean_stated": sum(p for p, _ in members) / len(members),
                    "hit_rate": sum(o for _, o in members) / len(members),
                }
            )
        out.append(
            {
                "bot_id": bot,
                "strategy_bucket": bucket,
                "samples": len(samples),
                "bins": bins,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 4b-2 panel 6 — Flow Lens consensus/novelty board (reads the OPS db)
# ---------------------------------------------------------------------------


def flow_lens_board(db_path: str | Path, *, limit: int = 25) -> dict[str, Any]:
    """Latest day's consensus board from the Flow Lens tables."""
    with _connect(db_path) as conn:
        latest = conn.execute(
            "SELECT MAX(logged_date) AS d FROM flow_lens_consensus"
        ).fetchone()
        logged_date = latest["d"] if latest else None
        if not logged_date:
            return {"logged_date": None, "rows": []}
        rows = conn.execute(
            """
            SELECT symbol, consensus_score, novelty_flag, model_count, prompt_count, models_json
            FROM flow_lens_consensus
            WHERE logged_date = ?
            ORDER BY consensus_score DESC, symbol ASC
            LIMIT ?
            """,
            (logged_date, limit),
        ).fetchall()
    return {
        "logged_date": logged_date,
        "rows": [
            {
                "symbol": r["symbol"],
                "consensus_score": r["consensus_score"],
                "novelty_flag": bool(r["novelty_flag"]),
                "model_count": r["model_count"],
                "prompt_count": r["prompt_count"],
                "models": json.loads(r["models_json"]),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Shot 13 — AI debate view + gate scoreboard + Tier-2 AI-value trackers
# ---------------------------------------------------------------------------

#: Signals (any fade gate decision) before the falsification sample is "meaningful"
FADE_FALSIFIER_N = 40
#: Pre-registered fade falsifier (shot 5): reversion hit-rate >= 55% AND positive
#: net return after costs, over the first FADE_FALSIFIER_N signals. NOT 50%.
FADE_REVERSION_BAR = 0.55
#: Calibration bars (mirrors CALIBRATION_* but named for the scoreboard context)
GATE_SCOREBOARD_RIDE_MIN_SAMPLES: int = CALIBRATION_MIN_SAMPLES   # 10
GATE_SCOREBOARD_RIDE_BRIER_BAR: float = CALIBRATION_MAX_MEAN_BRIER  # 0.25
#: Sector-neutral sign-accuracy bar for Frontier
GATE_SCOREBOARD_FRONTIER_BAR: float = 0.55


def fade_debates_index(
    db_path: str | Path, *, limit: int = 25
) -> list[dict[str, Any]]:
    """Recent fade gate decisions (entered, vetoed, or skipped) with debate summary.

    Newest first. One row per decision that carries a ``fade_gate`` JSON key.
    Used by the /debates index page.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.decision_id, d.thesis_id, d.action, d.as_of, d.prompt_version,
                   d.inputs_json, t.symbol, t.strategy_bucket, t.outcome
            FROM decisions d
            LEFT JOIN theses t ON d.thesis_id = t.thesis_id
            WHERE d.decision_type = 'entry'
              AND json_extract(d.inputs_json, '$.fade_gate') IS NOT NULL
            ORDER BY d.as_of DESC, d.decision_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        inputs = json.loads(r["inputs_json"])
        fg = inputs.get("fade_gate") or {}
        flags = list(fg.get("flags") or [])
        cp = fg.get("counter_pass") or {}
        out.append(
            {
                "decision_id": r["decision_id"],
                "thesis_id": r["thesis_id"],
                "symbol": r["symbol"] or inputs.get("symbol"),
                "as_of": r["as_of"],
                "prompt_version": r["prompt_version"],
                "action": r["action"],
                "entered": r["action"] in ("buy", "entered"),
                "vetoed": bool(fg.get("vetoed", False)),
                "ensemble_probability": fg.get("ensemble_probability"),
                "stated_probability": fg.get("stated_probability"),
                "classification": fg.get("classification"),
                "flags": flags,
                "ensemble_disagreement": "ensemble_disagreement" in flags,
                "ensemble_unavailable": "ensemble_unavailable" in flags,
                "has_argument": bool(str(cp.get("argument", "")).strip()),
                "outcome": r["outcome"],
                "strategy_bucket": r["strategy_bucket"] or "crash:fade",
            }
        )
    return out


def model_disagreement_stats(db_path: str | Path, *, limit: int = 500) -> dict[str, Any]:
    """Tier 2: how often do the two rubric models disagree across all fade decisions.

    Returns counts and rates; ``total=0`` when there is no fade data yet.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.inputs_json, d.prompt_version, t.outcome
            FROM decisions d
            LEFT JOIN theses t ON d.thesis_id = t.thesis_id
            WHERE d.decision_type = 'entry'
              AND json_extract(d.inputs_json, '$.fade_gate') IS NOT NULL
            ORDER BY d.as_of DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    total = 0
    disagreements = 0
    unavailable = 0
    for r in rows:
        inputs = json.loads(r["inputs_json"])
        flags = list((inputs.get("fade_gate") or {}).get("flags") or [])
        total += 1
        if "ensemble_disagreement" in flags:
            disagreements += 1
        if "ensemble_unavailable" in flags or "cross_model_failed" in flags:
            unavailable += 1
    return {
        "total": total,
        "disagreements": disagreements,
        "disagree_rate": (disagreements / total) if total else None,
        "ensemble_unavailable": unavailable,
        "unavail_rate": (unavailable / total) if total else None,
    }


def veto_value_tally(db_path: str | Path, *, limit: int = 200) -> dict[str, Any]:
    """Tier 2: of fades the counter-pass VETOED, fraction that would have won vs lost.

    Reads ``decisions`` + ``theses``; split by prompt_version so v1 vs v2 are
    comparable.  Returns ``meaningful=False`` until ≥10 resolved vetoes exist.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.inputs_json, d.prompt_version, t.outcome
            FROM decisions d
            LEFT JOIN theses t ON d.thesis_id = t.thesis_id
            WHERE d.decision_type = 'entry'
              AND d.action = 'fade_vetoed'
            ORDER BY d.as_of DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    by_version: dict[str, dict[str, Any]] = {}
    for r in rows:
        version = r["prompt_version"] or "unknown"
        entry = by_version.setdefault(
            version, {"total": 0, "resolved": 0, "would_have_won": 0}
        )
        entry["total"] += 1
        if r["outcome"] is not None:
            entry["resolved"] += 1
            if int(r["outcome"]) == 1:
                entry["would_have_won"] += 1
    total_vetoed = sum(v["total"] for v in by_version.values())
    total_resolved = sum(v["resolved"] for v in by_version.values())
    return {
        "total_vetoed": total_vetoed,
        "total_resolved": total_resolved,
        "meaningful": total_resolved >= 10,
        "by_version": by_version,
    }


# ---------------------------------------------------------------------------
# Dashboard v3 Phase 1 — strategy-agnostic registry + data layer
# ---------------------------------------------------------------------------
#
# All bot_id literals live in _LANE_REGISTRY below. Query bodies resolve lane
# identity through engines()/strategies() — never hardcode bot_id strings in SQL.


@dataclass(frozen=True)
class LaneRegistryEntry:
    bot_id: str
    role: str  # brain | control | brain_v2 | rehearsal
    tradeable: bool
    predict_only: bool = False
    buckets: frozenset[str] | None = None  # None = inherit strategy buckets


@dataclass(frozen=True)
class StrategyRegistryEntry:
    strategy_id: str
    engine_id: str
    label: str
    buckets: frozenset[str]
    lanes: tuple[LaneRegistryEntry, ...]
    gate_kind: str  # brier | fade_signals | sign_accuracy
    race_kind: str  # pairwise | three_way | sign_accuracy
    scoreboard: bool = True  # False for ops-only strategies (shadow)


_ENGINE_LABELS: dict[str, str] = {
    "proportionality": "Proportionality",
    "frontier": "Frontier",
    "lens": "Lens",
    "ops": "Ops",
}

# Central mapping — the only place bot_id string literals are defined.
_LANE_REGISTRY: tuple[StrategyRegistryEntry, ...] = (
    StrategyRegistryEntry(
        strategy_id="earnings_ride",
        engine_id="proportionality",
        label="Earnings ride",
        buckets=frozenset({"earnings:ride"}),
        lanes=(
            LaneRegistryEntry("proportionality", "brain", True),
            LaneRegistryEntry("proportionality_mech", "control", True),
        ),
        gate_kind="brier",
        race_kind="pairwise",
    ),
    StrategyRegistryEntry(
        strategy_id="crash_fade",
        engine_id="proportionality",
        label="Crash fade",
        buckets=frozenset({"crash:fade", "crash:fade_v2"}),
        lanes=(
            LaneRegistryEntry(
                "proportionality", "brain", True, buckets=frozenset({"crash:fade"})
            ),
            LaneRegistryEntry("crash_fade_v2", "brain_v2", True),
            LaneRegistryEntry("crash_fade_mech", "control", True),
        ),
        gate_kind="fade_signals",
        race_kind="three_way",
    ),
    StrategyRegistryEntry(
        strategy_id="frontier_up",
        engine_id="frontier",
        label="Propagation up",
        buckets=frozenset(),  # filled dynamically: frontier:*_up
        lanes=(
            LaneRegistryEntry("frontier", "brain", True),
            LaneRegistryEntry("frontier_mech", "control", True),
        ),
        gate_kind="sign_accuracy",
        race_kind="sign_accuracy",
    ),
    StrategyRegistryEntry(
        strategy_id="frontier_down",
        engine_id="frontier",
        label="Propagation down",
        buckets=frozenset(),  # filled dynamically: frontier:* without _up
        lanes=(
            LaneRegistryEntry("frontier", "brain", False, predict_only=True),
            LaneRegistryEntry("frontier_mech", "control", False, predict_only=True),
        ),
        gate_kind="sign_accuracy",
        race_kind="sign_accuracy",
    ),
    StrategyRegistryEntry(
        strategy_id="shadow_rehearsal",
        engine_id="ops",
        label="Shadow rehearsal",
        buckets=frozenset({"earnings:ride", "crash:fade"}),
        lanes=(LaneRegistryEntry("proportionality_shadow", "rehearsal", False),),
        gate_kind="brier",
        race_kind="pairwise",
        scoreboard=False,
    ),
)

_REGISTRY_BY_ID: dict[str, StrategyRegistryEntry] = {
    s.strategy_id: s for s in _LANE_REGISTRY
}

_EXIT_REASON_LABELS: dict[str, str] = {
    "hard_stop": "hard_stop",
    "fade_hard_stop": "hard_stop",
    "trailing_stop": "trailing_stop",
    "time_stop": "time_stop",
    "fade_time_stop": "time_stop",
    "profit_take_partial": "profit-target",
    # Frontier unwinds its long at the pre-registered holding horizon and logs
    # that as a 'resolution' decision (sign_correct/sign_wrong), never an
    # 'exit' — so these round trips read as "unknown" without this mapping.
    "sign_correct_up": "horizon",
    "sign_wrong_up": "horizon",
    "sign_correct": "horizon",
    "sign_wrong": "horizon",
}

SCOREBOARD_FRONTIER_MIN_SAMPLES = 30
CRUMBS_STALE_HOURS = 36.0


def _strategy_spec(strategy_id: str) -> StrategyRegistryEntry:
    spec = _REGISTRY_BY_ID.get(strategy_id)
    if spec is None:
        raise KeyError(f"unknown strategy_id: {strategy_id}")
    return spec


def _frontier_bucket_matches(strategy_id: str, bucket: str | None) -> bool:
    if not bucket or not bucket.startswith("frontier:"):
        return False
    if strategy_id == "frontier_up":
        return bucket.endswith("_up")
    if strategy_id == "frontier_down":
        return not bucket.endswith("_up")
    return False


def _lane_buckets(spec: StrategyRegistryEntry, lane: LaneRegistryEntry) -> frozenset[str]:
    if lane.buckets is not None:
        return lane.buckets
    return spec.buckets


def _bucket_matches_strategy(strategy_id: str, bucket: str | None) -> bool:
    spec = _strategy_spec(strategy_id)
    if spec.engine_id == "frontier" and strategy_id.startswith("frontier_"):
        return _frontier_bucket_matches(strategy_id, bucket)
    b = bucket or "(untagged)"
    return b in spec.buckets


def _active_bot_ids(db_path: str | Path) -> set[str]:
    """Distinct bot_ids with any persisted dashboard-relevant rows."""
    if not db_exists(db_path):
        return set()
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT bot_id FROM portfolio_snapshots
            UNION SELECT bot_id FROM fills
            UNION SELECT bot_id FROM theses
            UNION SELECT bot_id FROM calibration_ledger
            UNION SELECT bot_id FROM decisions
            """
        ).fetchall()
    return {r["bot_id"] for r in rows}


def _lane_dict(spec: StrategyRegistryEntry, lane: LaneRegistryEntry, active: bool) -> dict[str, Any]:
    return {
        "bot_id": lane.bot_id,
        "role": lane.role,
        "tradeable": lane.tradeable,
        "predict_only": lane.predict_only,
        "buckets": sorted(_lane_buckets(spec, lane)),
        "active": active,
    }


def strategies(db_path: str | Path) -> list[dict[str, Any]]:
    """Flat registry of engine → strategy → lanes (metadata + DB presence)."""
    active = _active_bot_ids(db_path)
    out: list[dict[str, Any]] = []
    for spec in _LANE_REGISTRY:
        lanes = [_lane_dict(spec, lane, lane.bot_id in active) for lane in spec.lanes]
        out.append(
            {
                "strategy_id": spec.strategy_id,
                "engine_id": spec.engine_id,
                "engine_label": _ENGINE_LABELS.get(spec.engine_id, spec.engine_id),
                "label": spec.label,
                "buckets": sorted(spec.buckets) if spec.buckets else [],
                "gate_kind": spec.gate_kind,
                "race_kind": spec.race_kind,
                "scoreboard": spec.scoreboard,
                "lanes": lanes,
            }
        )
    return out


def engines(db_path: str | Path) -> list[dict[str, Any]]:
    """Engine-grouped view of ``strategies()`` (for home scoreboard sections)."""
    by_engine: dict[str, list[dict[str, Any]]] = {}
    for s in strategies(db_path):
        by_engine.setdefault(s["engine_id"], []).append(s)
    order = ("proportionality", "frontier", "lens", "ops")
    out: list[dict[str, Any]] = []
    for engine_id in order:
        strats = by_engine.get(engine_id)
        if not strats:
            continue
        scoreboard_strats = [s for s in strats if s["scoreboard"]]
        if not scoreboard_strats:
            continue
        out.append(
            {
                "engine_id": engine_id,
                "label": _ENGINE_LABELS.get(engine_id, engine_id),
                "strategies": scoreboard_strats,
            }
        )
    return out


def _lane_bot_ids_for_strategy(strategy_id: str) -> list[str]:
    spec = _strategy_spec(strategy_id)
    return [lane.bot_id for lane in spec.lanes]


def _calibration_for_strategy(
    db_path: str | Path, strategy_id: str
) -> list[dict[str, Any]]:
    rows = calibration_by_strategy_bucket(db_path)
    return [r for r in rows if _bucket_matches_strategy(strategy_id, r["strategy_bucket"])]


def _gate_status_ride(stats: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    n = int(stats.get("n") or 0)
    mean_brier = stats.get("mean_brier")
    needed = GATE_SCOREBOARD_RIDE_MIN_SAMPLES
    bar = GATE_SCOREBOARD_RIDE_BRIER_BAR
    gate = {
        "metric": "mean_brier",
        "value": mean_brier,
        "bar": bar,
        "sample_n": n,
        "needed": needed,
        "progress": min(n / needed, 1.0) if needed else 0.0,
        "hit_rate": stats.get("hit_rate"),
    }
    if n == 0:
        return "too_early", gate
    if n < needed:
        return "measuring", gate
    if mean_brier is not None and mean_brier <= bar:
        return "passing", gate
    return "failing", gate


def _gate_status_fade(
    db_path: str | Path,
    stats: dict[str, Any],
    *,
    brain_bot_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    with _connect(db_path) as conn:
        # 'evaluated' = crashes the BRAIN lane's gate looked at. Without the bot
        # filter this counted the shadow/v2 lanes' mirrored decisions too (~3x).
        where = [
            "decision_type = 'entry'",
            "json_extract(inputs_json, '$.fade_gate') IS NOT NULL",
        ]
        params: list[Any] = []
        if brain_bot_id is not None:
            where.append("bot_id = ?")
            params.append(brain_bot_id)
        signals = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM decisions WHERE {' AND '.join(where)}",
                params,
            ).fetchone()["n"]
        )
    resolved = int(stats.get("n") or 0)
    hit_rate = stats.get("hit_rate")
    needed = FADE_FALSIFIER_N
    # Net return after costs = realized P/L of the BRAIN lane's fade round trips
    # (bucket crash:fade); the v2 and coin lanes are scored separately. The
    # falsifier needs BOTH reversion >= 55% AND positive net return — hit-rate
    # alone is not "passing".
    spec = _strategy_spec("crash_fade")
    brain = next((l for l in spec.lanes if l.role == "brain"), None)
    net_pl = round(
        sum(
            t["realized_pl"]
            for t in closed_trades(db_path, strategy_id="crash_fade")
            if brain is not None and t["bot_id"] == brain.bot_id
        ),
        2,
    )
    gate = {
        "metric": "fade_signals",
        "value": resolved,
        "bar": needed,
        "sample_n": resolved,          # falsifier is measured on RESOLVED fades
        "needed": needed,
        "progress": min(resolved / needed, 1.0) if needed else 0.0,
        "resolved_n": resolved,
        "evaluated": signals,          # crashes the gate looked at (mostly skips) — NOT the sample
        "reversion_rate": hit_rate,
        "reversion_bar": FADE_REVERSION_BAR,
        "net_pl": net_pl,
    }
    # Pre-registered falsifier (shot 5) is judged on RESOLVED fades — reversion
    # can't be measured on a skip, so the denominator is fades that resolved, not
    # crashes the gate evaluated. Until 40 resolve it's measuring, never "failing".
    if resolved == 0:
        return "too_early", gate
    if resolved < needed:
        return "measuring", gate
    if hit_rate is not None and hit_rate >= FADE_REVERSION_BAR and net_pl > 0:
        return "passing", gate
    return "failing", gate


def _gate_status_frontier(stats: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    n = int(stats.get("n") or 0)
    hit_rate = stats.get("hit_rate")
    bar = GATE_SCOREBOARD_FRONTIER_BAR
    needed = SCOREBOARD_FRONTIER_MIN_SAMPLES
    gate = {
        "metric": "sign_accuracy",
        "value": hit_rate,
        "bar": bar,
        "sample_n": n,
        "needed": needed,
        "progress": min(n / needed, 1.0) if needed else 0.0,
    }
    if n == 0:
        return "too_early", gate
    if n < needed:
        return "measuring", gate
    if hit_rate is not None and hit_rate >= bar:
        return "passing", gate
    return "failing", gate


def _aggregate_calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mean_brier": None, "hit_rate": None}
    total_n = sum(int(r["count"]) for r in rows)
    if total_n == 0:
        return {"n": 0, "mean_brier": None, "hit_rate": None}
    mean_brier_vals = [
        float(r["mean_brier"]) * int(r["count"])
        for r in rows
        if r.get("mean_brier") is not None
    ]
    hit_vals = [
        float(r["hit_rate"]) * int(r["count"])
        for r in rows
        if r.get("hit_rate") is not None
    ]
    return {
        "n": total_n,
        "mean_brier": (sum(mean_brier_vals) / total_n) if mean_brier_vals else None,
        "hit_rate": (sum(hit_vals) / total_n) if hit_vals else None,
    }


def _brain_vs_control_standing(
    db_path: str | Path, strategy_id: str, *, mode: str = "paper"
) -> dict[str, Any]:
    """Brain-minus-control edge from each lane's bucket-attributed realized P/L."""
    spec = _strategy_spec(strategy_id)
    brain = next((l for l in spec.lanes if l.role == "brain"), None)
    control = next((l for l in spec.lanes if l.role == "control"), None)
    if brain is None or control is None:
        return {
            "brain_bot_id": None,
            "control_bot_id": None,
            "edge": None,
            "basis": "realized_pl",
        }

    trades = closed_trades(db_path, strategy_id=strategy_id, mode=mode)
    brain_buckets = _lane_buckets(spec, brain)
    control_buckets = _lane_buckets(spec, control)
    all_buckets = brain_buckets | control_buckets

    def _lane_pl(lane: LaneRegistryEntry, buckets: frozenset[str]) -> float:
        # Frontier buckets are suffix-matched (frontier:*_up vs frontier:*), not a
        # fixed set — set-membership silently dropped every frontier trade.
        def _matches(bucket: str | None) -> bool:
            if spec.strategy_id.startswith("frontier_"):
                return _bucket_matches_strategy(spec.strategy_id, bucket)
            return (bucket or "(untagged)") in buckets

        return sum(
            float(t["realized_pl"])
            for t in trades
            if t["bot_id"] == lane.bot_id and _matches(t.get("strategy_bucket"))
        )

    brain_pl = _lane_pl(brain, brain_buckets)
    control_pl = _lane_pl(control, control_buckets)
    has_trades = any(
        t["bot_id"] in (brain.bot_id, control.bot_id)
        and (t.get("strategy_bucket") or "(untagged)") in all_buckets
        for t in trades
    )
    return {
        "brain_bot_id": brain.bot_id,
        "control_bot_id": control.bot_id,
        "brain_pl": brain_pl,
        "control_pl": control_pl,
        "edge": (brain_pl - control_pl) if has_trades else None,
        "basis": "realized_pl",
    }


def _equity_return(curve: list[dict[str, Any]]) -> float | None:
    if len(curve) < 2:
        return None
    start = float(curve[0]["total_equity"])
    end = float(curve[-1]["total_equity"])
    if start <= 0:
        return None
    return (end - start) / start


def scoreboard(db_path: str | Path) -> dict[str, Any]:
    """Per-strategy summaries for home scoreboard cards (handles n=0)."""
    cal_rows = calibration_by_strategy_bucket(db_path)
    cards: list[dict[str, Any]] = []
    for spec in _LANE_REGISTRY:
        if not spec.scoreboard:
            continue
        strat_cal = [
            r for r in cal_rows if _bucket_matches_strategy(spec.strategy_id, r["strategy_bucket"])
        ]
        # The pre-registered gates are judged on the BRAIN lane's own bucket
        # (per-bot per-bucket, like the capital gate) — blending the coin twin's
        # resolutions into the sample overstates progress and mixes its Brier in.
        brain_lane = next((l for l in spec.lanes if l.role == "brain"), None)
        gate_cal = (
            [r for r in strat_cal if r["bot_id"] == brain_lane.bot_id]
            if brain_lane is not None
            else strat_cal
        )
        stats = _aggregate_calibration(gate_cal)
        if spec.gate_kind == "brier":
            status, gate = _gate_status_ride(stats)
        elif spec.gate_kind == "fade_signals":
            fade_cal = [r for r in gate_cal if r["strategy_bucket"] == "crash:fade"]
            status, gate = _gate_status_fade(
                db_path,
                _aggregate_calibration(fade_cal),
                brain_bot_id=brain_lane.bot_id if brain_lane else None,
            )
        else:
            status, gate = _gate_status_frontier(stats)
        cards.append(
            {
                "strategy_id": spec.strategy_id,
                "engine_id": spec.engine_id,
                "engine_label": _ENGINE_LABELS.get(spec.engine_id, spec.engine_id),
                "label": spec.label,
                "status": status,
                "sample_n": gate.get("sample_n", stats["n"]),
                "gate": gate,
                "standing": _brain_vs_control_standing(db_path, spec.strategy_id),
            }
        )
    return {"strategies": cards, "engines": engines(db_path)}


def strategy_detail(
    db_path: str | Path,
    strategy_id: str,
    *,
    mode: str = "paper",
    sidecar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything about one strategy: lanes, positions, calibration, race, debates."""
    spec = _strategy_spec(strategy_id)
    active = _active_bot_ids(db_path)
    lanes = [_lane_dict(spec, lane, lane.bot_id in active) for lane in spec.lanes]

    # A lane shared by multiple strategies (proportionality trades ride AND
    # fade) must not show the OTHER strategy's positions on this page. The
    # sidecar's open trades carry `trade_kind`, so partition by it: the fade
    # page keeps kind=='fade', the ride page keeps everything else.
    shared_lane_ids = {
        lane.bot_id
        for other in _LANE_REGISTRY
        if other.strategy_id != spec.strategy_id
        for lane in other.lanes
    } & {lane.bot_id for lane in spec.lanes}
    want_fade = "fade" in (spec.buckets or frozenset()) or any(
        "fade" in b for b in (spec.buckets or frozenset())
    )

    open_by_lane: dict[str, dict[str, Any]] = {}
    for lane in spec.lanes:
        if not lane.tradeable:
            continue
        trades = None
        if sidecar is not None:
            lane_state = sidecar.get(lane.bot_id) or {}
            trades = (lane_state.get("strategy") or {}).get("open") or []
        lane_view = open_positions(
            db_path, lane.bot_id, mode, trades if isinstance(trades, list) else None
        )
        if lane.bot_id in shared_lane_ids and isinstance(trades, list) and trades:
            keep = {
                t.get("symbol")
                for t in trades
                if (t.get("trade_kind") == "fade") == want_fade
            }
            lane_view = dict(lane_view)
            lane_view["positions"] = [
                p for p in lane_view["positions"] if p.get("symbol") in keep
            ]
            lane_view["partitioned_by_trade_kind"] = True
        open_by_lane[lane.bot_id] = lane_view

    cal_filtered = [
        c
        for c in calibration_curves(db_path)
        if _bucket_matches_strategy(strategy_id, c["strategy_bucket"])
    ]

    detail: dict[str, Any] = {
        "strategy_id": strategy_id,
        "engine_id": spec.engine_id,
        "label": spec.label,
        "lanes": lanes,
        "open_positions": open_by_lane,
        "calibration": cal_filtered,
        "calibration_summary": _aggregate_calibration(_calibration_for_strategy(db_path, strategy_id)),
        "race": race(db_path, strategy_id, mode=mode),
        "standing": _brain_vs_control_standing(db_path, strategy_id, mode=mode),
        "closed_trades": closed_trades(db_path, strategy_id=strategy_id, mode=mode),
    }
    if strategy_id == "crash_fade":
        detail["debates"] = fade_debates_index(db_path)
    else:
        detail["debates"] = []
    return detail


def _realized_pl_dollars(entry_price: float, exit_price: float, quantity: float) -> float:
    """Realized $ P/L from the actual fill prices; round only the final dollars.

    Simulated fills carry sub-cent slippage (e.g. 159.38394); rounding the prices
    to whole cents BEFORE multiplying loses that precision and misstates P/L (and
    the error compounds across trades). Compute from the stored prices and round
    once, at the end. Live cent-granular fills work identically (sub-cent part 0).
    """
    return round((exit_price - entry_price) * quantity, 2)


def _normalize_exit_reason(action: str | None) -> str:
    if not action:
        return "unknown"
    return _EXIT_REASON_LABELS.get(action, action)


def _exit_reason_for_fill(
    db_path: str | Path,
    *,
    bot_id: str,
    thesis_id: str | None,
    sell_filled_at: str,
) -> str:
    if not thesis_id:
        return "unknown"
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT action FROM decisions
            WHERE bot_id = ? AND thesis_id = ?
              AND decision_type IN ('exit', 'resolution')
              AND as_of <= ?
            ORDER BY as_of DESC, decision_id DESC
            LIMIT 1
            """,
            (bot_id, thesis_id, sell_filled_at),
        ).fetchone()
    return _normalize_exit_reason(row["action"] if row else None)


def closed_trades(
    db_path: str | Path,
    *,
    strategy_id: str | None = None,
    mode: str = "paper",
) -> list[dict[str, Any]]:
    """FIFO pairing of buy/sell fills into round trips with realized $ P/L.

    ``strategy_id=None`` returns all tradeable strategies (Execution page).
    Open lots (buy without matching sell) are excluded.

    Multi-lot: standard FIFO per (bot_id, symbol). Partial exits produce one
    closed row per matched lot slice.
    """
    if not db_exists(db_path):
        return []

    if strategy_id is not None:
        specs = [_strategy_spec(strategy_id)]
    else:
        specs = [s for s in _LANE_REGISTRY if s.scoreboard and any(l.tradeable for l in s.lanes)]

    bot_ids: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        for lane in spec.lanes:
            if not lane.tradeable or lane.bot_id in seen:
                continue
            seen.add(lane.bot_id)
            bot_ids.append(lane.bot_id)

    if not bot_ids:
        return []

    def _fill_matches(row: sqlite3.Row) -> bool:
        bucket = row["strategy_bucket"]
        bid = row["bot_id"]
        for spec in specs:
            for lane in spec.lanes:
                if not lane.tradeable or lane.bot_id != bid:
                    continue
                if spec.strategy_id.startswith("frontier_"):
                    if _bucket_matches_strategy(spec.strategy_id, bucket):
                        return True
                    continue
                lane_buckets = _lane_buckets(spec, lane)
                if (bucket or "(untagged)") in lane_buckets:
                    return True
        return False

    placeholders = ",".join("?" * len(bot_ids))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT f.fill_id, f.bot_id, f.symbol, f.side, f.quantity, f.price,
                   f.filled_at, f.thesis_id, t.strategy_bucket
            FROM fills f
            LEFT JOIN theses t ON t.thesis_id = f.thesis_id
            WHERE f.mode = ? AND f.bot_id IN ({placeholders})
            ORDER BY f.filled_at ASC, f.fill_id ASC
            """,
            (mode, *bot_ids),
        ).fetchall()

    grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in rows:
        if not _fill_matches(r):
            continue
        grouped.setdefault((r["bot_id"], r["symbol"]), []).append(r)

    closed: list[dict[str, Any]] = []
    for (bot_id, symbol), fills in grouped.items():
        lots: list[dict[str, Any]] = []
        for r in fills:
            side = str(r["side"]).lower()
            qty = float(r["quantity"])
            if side == "buy":
                lots.append(
                    {
                        "qty_remaining": qty,
                        "entry_price": float(r["price"]),
                        "entry_at": r["filled_at"],
                        "thesis_id": r["thesis_id"],
                    }
                )
                continue
            if side != "sell":
                continue
            sell_qty = qty
            sell_price = float(r["price"])
            sell_at = r["filled_at"]
            while sell_qty > 1e-9 and lots:
                lot = lots[0]
                matched = min(sell_qty, lot["qty_remaining"])
                entry_price = lot["entry_price"]
                pl = _realized_pl_dollars(entry_price, sell_price, matched)
                entry_dt = datetime.fromisoformat(lot["entry_at"])
                exit_dt = datetime.fromisoformat(sell_at)
                hold_days = (exit_dt - entry_dt).total_seconds() / 86400.0
                closed.append(
                    {
                        "bot_id": bot_id,
                        "symbol": symbol,
                        "strategy_bucket": r["strategy_bucket"],
                        "entry_price": entry_price,
                        "exit_price": sell_price,
                        "quantity": matched,
                        "realized_pl": pl,
                        "exit_reason": _exit_reason_for_fill(
                            db_path,
                            bot_id=bot_id,
                            thesis_id=r["thesis_id"] or lot["thesis_id"],
                            sell_filled_at=sell_at,
                        ),
                        "hold_days": round(hold_days, 2),
                        "entry_at": lot["entry_at"],
                        "exit_at": sell_at,
                        "thesis_id": r["thesis_id"] or lot["thesis_id"],
                    }
                )
                lot["qty_remaining"] -= matched
                sell_qty -= matched
                if lot["qty_remaining"] <= 1e-9:
                    lots.pop(0)

    closed.sort(key=lambda t: (t["exit_at"], t["bot_id"], t["symbol"]), reverse=True)
    return closed


def race(
    db_path: str | Path,
    strategy_id: str,
    *,
    mode: str = "paper",
) -> dict[str, Any]:
    """Strategy-scoped brain-vs-control race (2-way, 3-way, or sign-accuracy)."""
    spec = _strategy_spec(strategy_id)
    if spec.race_kind == "pairwise":
        out = _race_pairwise(db_path, spec, mode=mode)
    elif spec.race_kind == "three_way":
        out = _race_three_way(db_path, spec, mode=mode)
    else:
        out = _race_sign_accuracy(db_path, spec, mode=mode)

    # Honesty label: equity CURVES are each lane's whole book. When a lane is
    # shared across strategies (proportionality = ride + fade), its curve
    # commingles both; the per-event table / standing / gates are bucket-scoped
    # and remain the clean per-strategy comparison.
    lane_ids = {lane.bot_id for lane in spec.lanes}
    shared = {
        lane.bot_id
        for other in _LANE_REGISTRY
        if other.strategy_id != spec.strategy_id
        for lane in other.lanes
    } & lane_ids
    if shared and isinstance(out, dict):
        out["curves_note"] = (
            "equity curves show each lane's FULL book — "
            f"{', '.join(sorted(shared))} trades more than one strategy, so its "
            "curve commingles them; the per-event table and standing above are "
            "bucket-scoped and compare only this strategy"
        )
    return out


def _race_pairwise(
    db_path: str | Path, spec: StrategyRegistryEntry, *, mode: str
) -> dict[str, Any]:
    """Two-lane race (ride): brain vs control, bucket-scoped when needed."""
    brain = next(l for l in spec.lanes if l.role == "brain")
    control = next(l for l in spec.lanes if l.role == "control")
    lanes = {brain.bot_id: "gated", control.bot_id: "mech"}
    curves = {bot: equity_curve(db_path, bot, mode) for bot in lanes}
    per_event: dict[str, dict[str, Any]] = {}
    summary: dict[str, dict[str, Any]] = {}

    with _connect(db_path) as conn:
        for bot, key in lanes.items():
            rows = conn.execute(
                """
                SELECT action, inputs_json, as_of
                FROM decisions
                WHERE bot_id = ? AND mode = ? AND decision_type = 'resolution'
                ORDER BY as_of ASC, decision_id ASC
                """,
                (bot, mode),
            ).fetchall()
            returns: list[float] = []
            for r in rows:
                inputs = json.loads(r["inputs_json"])
                bucket = inputs.get("strategy_bucket")
                if not _bucket_matches_strategy(spec.strategy_id, bucket):
                    continue
                event_id = inputs.get("event_id") or f"(unknown:{r['as_of']})"
                entry = per_event.setdefault(
                    event_id,
                    {"event_id": event_id, "symbol": inputs.get("symbol"), "as_of": r["as_of"]},
                )
                net = inputs.get("net_return")
                entry[key] = net
                if net is not None:
                    returns.append(float(net))
            summary[key] = {
                "bot_id": bot,
                "resolved_trades": len(returns),
                "hit_rate": (sum(1 for x in returns if x > 0) / len(returns)) if returns else None,
                "mean_net_return": (sum(returns) / len(returns)) if returns else None,
            }
        skip_rows = conn.execute(
            """
            SELECT inputs_json, as_of
            FROM decisions
            WHERE bot_id = ? AND mode = ?
              AND decision_type = 'entry' AND action = 'skipped_quality_gate'
            ORDER BY as_of ASC, decision_id ASC
            """,
            (brain.bot_id, mode),
        ).fetchall()

    for r in skip_rows:
        inputs = json.loads(r["inputs_json"])
        bucket = inputs.get("strategy_bucket")
        if bucket and not _bucket_matches_strategy(spec.strategy_id, bucket):
            continue
        event_id = inputs.get("event_id") or f"(unknown:{r['as_of']})"
        entry = per_event.setdefault(
            event_id,
            {"event_id": event_id, "symbol": inputs.get("symbol"), "as_of": r["as_of"]},
        )
        entry["gate_skipped"] = True

    events = sorted(per_event.values(), key=lambda e: (e["as_of"], e["event_id"]))
    cum_gated = cum_mech = 0.0
    for e in events:
        if e.get("gated") is not None:
            cum_gated += float(e["gated"])
        if e.get("mech") is not None:
            cum_mech += float(e["mech"])
        e["cum_gated"] = cum_gated
        e["cum_mech"] = cum_mech

    return {
        "strategy_id": spec.strategy_id,
        "race_kind": "pairwise",
        "mode": mode,
        "lanes": [
            {"bot_id": brain.bot_id, "role": "brain"},
            {"bot_id": control.bot_id, "role": "control"},
        ],
        "curves": curves,
        "summary": summary,
        "events": events,
        "gated_bot_id": brain.bot_id,
        "mech_bot_id": control.bot_id,
    }


def _race_three_way(
    db_path: str | Path, spec: StrategyRegistryEntry, *, mode: str
) -> dict[str, Any]:
    """Fade: v1 brain vs v2 brain vs coin — joined on event_id."""
    lane_keys: dict[str, str] = {}
    for lane in spec.lanes:
        if lane.role == "brain":
            lane_keys[lane.bot_id] = "v1"
        elif lane.role == "brain_v2":
            lane_keys[lane.bot_id] = "v2"
        elif lane.role == "control":
            lane_keys[lane.bot_id] = "coin"
    bot_ids = list(lane_keys.keys())
    curves = {bot: equity_curve(db_path, bot, mode) for bot in bot_ids}
    per_event: dict[str, dict[str, Any]] = {}
    summary: dict[str, dict[str, Any]] = {}

    bucket_clause = ""
    params_base: list[Any] = []
    if spec.buckets:
        bucket_clause = " AND json_extract(inputs_json, '$.strategy_bucket') IN ({})".format(
            ",".join("?" * len(spec.buckets))
        )
        params_base = list(spec.buckets)

    with _connect(db_path) as conn:
        for bot, key in lane_keys.items():
            rows = conn.execute(
                f"""
                SELECT action, inputs_json, as_of
                FROM decisions
                WHERE bot_id = ? AND mode = ? AND decision_type = 'resolution'
                {bucket_clause}
                ORDER BY as_of ASC, decision_id ASC
                """,
                (bot, mode, *params_base),
            ).fetchall()
            returns: list[float] = []
            for r in rows:
                inputs = json.loads(r["inputs_json"])
                bucket = inputs.get("strategy_bucket")
                if not _bucket_matches_strategy(spec.strategy_id, bucket):
                    continue
                event_id = inputs.get("event_id") or f"(unknown:{r['as_of']})"
                entry = per_event.setdefault(
                    event_id,
                    {"event_id": event_id, "symbol": inputs.get("symbol"), "as_of": r["as_of"]},
                )
                net = inputs.get("net_return")
                entry[key] = net
                if net is not None:
                    returns.append(float(net))
            summary[key] = {
                "bot_id": bot,
                "resolved_trades": len(returns),
                "hit_rate": (sum(1 for x in returns if x > 0) / len(returns)) if returns else None,
                "mean_net_return": (sum(returns) / len(returns)) if returns else None,
            }

    events = sorted(per_event.values(), key=lambda e: (e["as_of"], e["event_id"]))
    cum = {k: 0.0 for k in lane_keys.values()}
    for e in events:
        for k in cum:
            if e.get(k) is not None:
                cum[k] += float(e[k])
            e[f"cum_{k}"] = cum[k]

    return {
        "strategy_id": spec.strategy_id,
        "race_kind": "three_way",
        "mode": mode,
        "lanes": [{"bot_id": bot, "role": key} for bot, key in lane_keys.items()],
        "curves": curves,
        "summary": summary,
        "events": events,
    }


def _race_sign_accuracy(
    db_path: str | Path, spec: StrategyRegistryEntry, *, mode: str
) -> dict[str, Any]:
    """Frontier up/down: sign-accuracy vs 55% bar; up side includes P&L curve."""
    brain = next(l for l in spec.lanes if l.role == "brain")
    control = next(l for l in spec.lanes if l.role == "control")
    cal = _calibration_for_strategy(db_path, spec.strategy_id)
    stats = _aggregate_calibration(cal)
    curves = {
        brain.bot_id: equity_curve(db_path, brain.bot_id, mode),
        control.bot_id: equity_curve(db_path, control.bot_id, mode),
    }
    return {
        "strategy_id": spec.strategy_id,
        "race_kind": "sign_accuracy",
        "mode": mode,
        "lanes": [
            {"bot_id": brain.bot_id, "role": "brain"},
            {"bot_id": control.bot_id, "role": "control"},
        ],
        "curves": curves,
        "summary": {
            "brain": {
                "bot_id": brain.bot_id,
                "sample_n": stats["n"],
                "hit_rate": stats["hit_rate"],
                "bar": GATE_SCOREBOARD_FRONTIER_BAR,
                "passing": (
                    stats["n"] >= SCOREBOARD_FRONTIER_MIN_SAMPLES
                    and stats["hit_rate"] is not None
                    and stats["hit_rate"] >= GATE_SCOREBOARD_FRONTIER_BAR
                ),
            },
            "control": {
                "bot_id": control.bot_id,
                "sample_n": stats["n"],
                "hit_rate": stats["hit_rate"],
            },
        },
        "events": [],
    }


def _parse_stored_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def last_productive_tick(db_path: str | Path, *, mode: str = "paper") -> str | None:
    """Timestamp of the last tick that actually wrote a portfolio snapshot.

    `last_run_info` reports the newest run ROW, which a crashed tick also
    leaves behind — during the 2026-08 outage that made a dead race read as
    "last tick <today> (completed)". Snapshots only exist when lanes ticked.
    """
    if not db_exists(db_path):
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(as_of) AS ts FROM portfolio_snapshots WHERE mode = ?",
            (mode,),
        ).fetchone()
    return row["ts"] if row and row["ts"] else None


def paper_tick_completed_today(
    db_path: str | Path,
    *,
    today: date | None = None,
    mode: str = "paper",
) -> bool:
    """True when the latest paper run completed on ``today`` (US Central calendar date).

    Used to reconcile ops health: the DB is authoritative for whether today's tick
    succeeded, even when the scheduled-task log still shows a stale failure exit code.
    """
    if not db_exists(db_path):
        return False
    run = last_run_info(db_path, mode=mode)
    if run is None or run.get("status") != "completed":
        return False
    ts = _parse_stored_ts(run.get("finished_at") or run.get("started_at"))
    if ts is None:
        return False
    day = today or datetime.now(_DISPLAY_TZ).date()
    if ts.astimezone(_DISPLAY_TZ).date() != day:
        return False
    # A run row is not evidence of work. A tick that crashed mid-lane still
    # recorded rows, so require a portfolio snapshot actually written today —
    # that only happens after every lane ticked successfully.
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT as_of FROM portfolio_snapshots WHERE mode = ? ORDER BY as_of DESC LIMIT 50",
            (mode,),
        ).fetchall()
    for row in rows:
        snap_ts = _parse_stored_ts(row["as_of"])
        if snap_ts is not None and snap_ts.astimezone(_DISPLAY_TZ).date() == day:
            return True
    return False


def last_run_info(db_path: str | Path, *, mode: str | None = None) -> dict[str, Any] | None:
    """Most recent runs-table row (ops strip: when did the paper tick last run)."""
    where, params = "1=1", []
    if mode is not None:
        where, params = "mode = ?", [mode]
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT run_id, bot_id, mode, started_at, finished_at, status
            FROM runs
            WHERE {where}
            ORDER BY started_at DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "bot_id": row["bot_id"],
        "mode": row["mode"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
    }

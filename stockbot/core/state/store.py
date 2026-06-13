"""SQLite schema: positions, cash, theses, events, calibration ledger."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

from stockbot.types import PortfolioSnapshot, Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    cash REAL NOT NULL,
    total_equity REAL NOT NULL,
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshots(id),
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    market_value REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    slippage_pct REAL NOT NULL DEFAULT 0,
    commission REAL NOT NULL DEFAULT 0,
    bot_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    filled_at TEXT NOT NULL,
    thesis_id TEXT,  -- 2-R3: direct fill->thesis join (NULL on pre-migration rows)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS theses (
    thesis_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    stated_probability REAL NOT NULL,
    rationale TEXT,
    mode TEXT NOT NULL,
    strategy_bucket TEXT NOT NULL DEFAULT '',  -- "<event_type>:<direction>", e.g. "earnings:ride"
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    outcome INTEGER  -- 1 = success, 0 = failure, NULL = open
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    as_of TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calibration_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id TEXT NOT NULL REFERENCES theses(thesis_id),
    bot_id TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    stated_probability REAL NOT NULL,
    outcome INTEGER NOT NULL,
    brier_score REAL NOT NULL,
    bucket TEXT NOT NULL,                       -- confidence bucket (low/medium/high_confidence)
    strategy_bucket TEXT NOT NULL DEFAULT '',   -- "<event_type>:<direction>"; capital is allocated/killed per this
    resolved_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    config_hash TEXT NOT NULL,
    model_versions_json TEXT NOT NULL DEFAULT '{}',
    git_sha TEXT,
    log_path TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(run_id),
    thesis_id TEXT REFERENCES theses(thesis_id),
    bot_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    action TEXT NOT NULL,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    model_id TEXT,
    prompt_version TEXT,
    mode TEXT NOT NULL,
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_calibration_bot_bucket ON calibration_ledger(bot_id, bucket);
CREATE INDEX IF NOT EXISTS idx_calibration_strategy_bucket ON calibration_ledger(bot_id, strategy_bucket);
CREATE INDEX IF NOT EXISTS idx_theses_bot ON theses(bot_id);
CREATE INDEX IF NOT EXISTS idx_fills_bot ON fills(bot_id);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_decisions_thesis ON decisions(thesis_id);
CREATE INDEX IF NOT EXISTS idx_runs_bot ON runs(bot_id);

CREATE TABLE IF NOT EXISTS flow_lens_samples (
    sample_id TEXT PRIMARY KEY,
    logged_date TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT,
    prompt_id TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    tickers_json TEXT NOT NULL DEFAULT '[]',
    sampled_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flow_lens_consensus (
    logged_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    consensus_score REAL NOT NULL,
    novelty_flag INTEGER NOT NULL DEFAULT 0,
    model_count INTEGER NOT NULL,
    prompt_count INTEGER NOT NULL,
    models_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (logged_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_flow_lens_samples_date ON flow_lens_samples(logged_date);
CREATE INDEX IF NOT EXISTS idx_flow_lens_consensus_date ON flow_lens_consensus(logged_date);

-- Frontier Engine Stage 1 (3a): typed-edge graph from SEC filings.
-- Materiality is certified by the disclosure itself (quoted passage), never
-- by an LLM's opinion. Read-only in shot 2 — nothing trades on this table.
CREATE TABLE IF NOT EXISTS frontier_edges (
    edge_id TEXT PRIMARY KEY,
    edge_type TEXT NOT NULL,            -- customer_concentration | sole_supplier | regulatory_precedent
    from_symbol TEXT NOT NULL,          -- the filer
    counterparty_name TEXT NOT NULL,    -- disclosed name, or e.g. "one customer" when anonymous
    counterparty_canonical TEXT NOT NULL DEFAULT '',  -- normalized identity for dedupe (3a-F)
    counterparty_symbol TEXT,           -- resolved ticker (conservative exact match) or NULL
    named INTEGER NOT NULL DEFAULT 0,   -- 1 = counterparty is a named entity (graph edge); 0 = anonymous (stats only)
    magnitude_pct REAL,                 -- e.g. % of revenue, when disclosed
    quoted_passage TEXT NOT NULL,       -- verbatim sentence(s) from the filing
    accession_number TEXT NOT NULL,
    filing_url TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT '10-K',
    filed_at TEXT NOT NULL,             -- filing date; edges are live <= 18 months after this
    extracted_at TEXT NOT NULL,
    model_id TEXT,                      -- LLM that confirmed the edge
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (from_symbol, accession_number, edge_type, counterparty_canonical)
);

CREATE INDEX IF NOT EXISTS idx_frontier_edges_from ON frontier_edges(from_symbol);
CREATE INDEX IF NOT EXISTS idx_frontier_edges_type ON frontier_edges(edge_type);

-- Phase 5a: live-broker manual-approval queue. When live_manual_approval is on,
-- the broker writes the proposed order here INSTEAD of placing it; the
-- approve_orders CLI lists and approves/rejects (the pilot's first-10 rule).
CREATE TABLE IF NOT EXISTS pending_approval_orders (
    approval_id TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    limit_price REAL,
    thesis_id TEXT,
    ref_id TEXT NOT NULL,            -- pre-minted idempotency key, reused on approval
    notional_est REAL,
    expected_price REAL,             -- shot 12: decision-time mark, carried to the fill for slippage
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | placed | failed
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    note TEXT,
    venue_order_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 5a: client-generated idempotency keys, persisted BEFORE the venue call
-- so a crash between persist and ack can be reconciled (re-send the SAME
-- ref_id; the venue dedupes). One row per logical live order.
CREATE TABLE IF NOT EXISTS broker_order_refs (
    ref_id TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL,
    thesis_id TEXT,
    venue_order_id TEXT,
    expected_price REAL,             -- shot 12: decision-time mark = the per-order reference price
    state TEXT NOT NULL DEFAULT 'created',  -- created | submitted | acked | partial | filled | failed
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_approval_status ON pending_approval_orders(status);
"""


class StateStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # Columns added after first release: migrated via ALTER TABLE on existing DBs,
    # because CREATE TABLE IF NOT EXISTS never alters an existing table. Migrations
    # must run BEFORE the schema script (the script creates indexes on these columns).
    _MIGRATIONS: tuple[tuple[str, str, str], ...] = (
        ("theses", "strategy_bucket", "TEXT NOT NULL DEFAULT ''"),
        ("calibration_ledger", "strategy_bucket", "TEXT NOT NULL DEFAULT ''"),
        ("fills", "thesis_id", "TEXT"),  # 2-R3: nullable — legacy fills stay NULL
        # shot 12: carry the decision-time reference price onto the live order so
        # realized-vs-expected slippage is a clean per-order computation.
        ("broker_order_refs", "expected_price", "REAL"),
        ("pending_approval_orders", "expected_price", "REAL"),
    )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            for table, column, decl in self._MIGRATIONS:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists is None:
                    continue  # schema script below creates it with the column included
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            # 3a-F: the edge dedupe key moved into the UNIQUE constraint
            # (counterparty_canonical), which ALTER TABLE cannot express. The
            # table is pure stage-1 extraction output, fully re-derivable from
            # the filing cache for pennies — drop the old shape and rebuild.
            edges_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='frontier_edges'"
            ).fetchone()
            if edges_exists is not None:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(frontier_edges)")}
                if "counterparty_canonical" not in cols:
                    conn.execute("DROP TABLE frontier_edges")
            conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_fill(
        self,
        fill_id: str,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        slippage_pct: float,
        bot_id: str,
        mode: str,
        filled_at: datetime,
        thesis_id: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fills
                (fill_id, order_id, symbol, side, quantity, price, slippage_pct, bot_id, mode,
                 filled_at, thesis_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill_id,
                    order_id,
                    symbol,
                    side,
                    quantity,
                    price,
                    slippage_pct,
                    bot_id,
                    mode,
                    filled_at.isoformat(),
                    thesis_id,
                ),
            )

    def get_fills(self, bot_id: str, mode: str | None = None) -> list[dict[str, Any]]:
        """Return persisted fills ordered by time. Broker-independent."""
        with self._connect() as conn:
            if mode is not None:
                rows = conn.execute(
                    """
                    SELECT fill_id, order_id, symbol, side, quantity, price, slippage_pct,
                           commission, bot_id, mode, filled_at, thesis_id
                    FROM fills
                    WHERE bot_id = ? AND mode = ?
                    ORDER BY filled_at ASC, fill_id ASC
                    """,
                    (bot_id, mode),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT fill_id, order_id, symbol, side, quantity, price, slippage_pct,
                           commission, bot_id, mode, filled_at, thesis_id
                    FROM fills
                    WHERE bot_id = ?
                    ORDER BY filled_at ASC, fill_id ASC
                    """,
                    (bot_id,),
                ).fetchall()
        return [
            {
                "fill_id": r["fill_id"],
                "order_id": r["order_id"],
                "symbol": r["symbol"],
                "side": r["side"],
                "quantity": r["quantity"],
                "price": r["price"],
                "slippage_pct": r["slippage_pct"],
                "commission": r["commission"],
                "bot_id": r["bot_id"],
                "mode": r["mode"],
                "filled_at": datetime.fromisoformat(r["filled_at"]),
                "thesis_id": r["thesis_id"],
            }
            for r in rows
        ]

    def record_snapshot(
        self,
        snapshot: PortfolioSnapshot,
        bot_id: str,
        mode: str,
    ) -> int:
        """Persist a portfolio snapshot + its positions. Returns the snapshot id.

        Writes the `portfolio_snapshots` and `positions` tables atomically so the
        equity curve can later be read straight from the DB (no broker in memory).
        """
        with self.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO portfolio_snapshots (mode, bot_id, cash, total_equity, as_of)
                VALUES (?, ?, ?, ?, ?)
                """,
                (mode, bot_id, snapshot.cash, snapshot.total_equity, snapshot.as_of.isoformat()),
            )
            snapshot_id = int(cur.lastrowid)
            if snapshot.positions:
                conn.executemany(
                    """
                    INSERT INTO positions
                    (snapshot_id, symbol, quantity, avg_cost, market_value, unrealized_pnl)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            snapshot_id,
                            p.symbol,
                            p.quantity,
                            p.avg_cost,
                            p.market_value,
                            p.unrealized_pnl,
                        )
                        for p in snapshot.positions
                    ],
                )
        return snapshot_id

    def get_equity_curve(self, bot_id: str, mode: str | None = None) -> list[dict[str, Any]]:
        """Return the persisted equity curve ordered by time. Broker-independent."""
        with self._connect() as conn:
            if mode is not None:
                rows = conn.execute(
                    """
                    SELECT id, as_of, cash, total_equity
                    FROM portfolio_snapshots
                    WHERE bot_id = ? AND mode = ?
                    ORDER BY as_of ASC, id ASC
                    """,
                    (bot_id, mode),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, as_of, cash, total_equity
                    FROM portfolio_snapshots
                    WHERE bot_id = ?
                    ORDER BY as_of ASC, id ASC
                    """,
                    (bot_id,),
                ).fetchall()
        return [
            {
                "id": r["id"],
                "as_of": datetime.fromisoformat(r["as_of"]),
                "cash": r["cash"],
                "total_equity": r["total_equity"],
            }
            for r in rows
        ]

    def get_latest_snapshot(self, bot_id: str, mode: str | None = None) -> PortfolioSnapshot | None:
        """Reconstruct the most recent persisted portfolio snapshot, or None."""
        with self._connect() as conn:
            if mode is not None:
                row = conn.execute(
                    """
                    SELECT id, cash, total_equity, as_of
                    FROM portfolio_snapshots
                    WHERE bot_id = ? AND mode = ?
                    ORDER BY as_of DESC, id DESC
                    LIMIT 1
                    """,
                    (bot_id, mode),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, cash, total_equity, as_of
                    FROM portfolio_snapshots
                    WHERE bot_id = ?
                    ORDER BY as_of DESC, id DESC
                    LIMIT 1
                    """,
                    (bot_id,),
                ).fetchone()
            if row is None:
                return None
            pos_rows = conn.execute(
                """
                SELECT symbol, quantity, avg_cost, market_value, unrealized_pnl
                FROM positions WHERE snapshot_id = ?
                """,
                (row["id"],),
            ).fetchall()
        positions = [
            Position(
                symbol=p["symbol"],
                quantity=p["quantity"],
                avg_cost=p["avg_cost"],
                market_value=p["market_value"],
                unrealized_pnl=p["unrealized_pnl"],
            )
            for p in pos_rows
        ]
        return PortfolioSnapshot(
            cash=row["cash"],
            positions=positions,
            total_equity=row["total_equity"],
            as_of=datetime.fromisoformat(row["as_of"]),
        )

    def record_thesis(
        self,
        thesis_id: str,
        bot_id: str,
        symbol: str,
        direction: str,
        stated_probability: float,
        mode: str,
        rationale: str | None = None,
        created_at: datetime | None = None,
        strategy_bucket: str = "",
    ) -> None:
        ts = (created_at or datetime.now(timezone.utc)).isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO theses
                (thesis_id, bot_id, symbol, direction, stated_probability, rationale, mode,
                 strategy_bucket, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thesis_id,
                    bot_id,
                    symbol,
                    direction,
                    stated_probability,
                    rationale,
                    mode,
                    strategy_bucket,
                    ts,
                ),
            )

    def resolve_thesis(self, thesis_id: str, outcome: bool, resolved_at: datetime | None = None) -> None:
        ts = (resolved_at or datetime.now(timezone.utc)).isoformat()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE theses SET outcome = ?, resolved_at = ? WHERE thesis_id = ?",
                (1 if outcome else 0, ts, thesis_id),
            )

    def record_calibration(
        self,
        thesis_id: str,
        bot_id: str,
        trade_type: str,
        stated_probability: float,
        outcome: bool,
        bucket: str,
        resolved_at: datetime | None = None,
        strategy_bucket: str = "",
    ) -> float:
        """Record a calibration entry and return the Brier score for this prediction."""
        brier = (stated_probability - (1.0 if outcome else 0.0)) ** 2
        ts = (resolved_at or datetime.now(timezone.utc)).isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO calibration_ledger
                (thesis_id, bot_id, trade_type, stated_probability, outcome, brier_score, bucket,
                 strategy_bucket, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thesis_id,
                    bot_id,
                    trade_type,
                    stated_probability,
                    1 if outcome else 0,
                    brier,
                    bucket,
                    strategy_bucket,
                    ts,
                ),
            )
        return brier

    def record_event(
        self,
        event_id: str,
        event_type: str,
        symbol: str,
        as_of: datetime,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO events (event_id, event_type, symbol, payload_json, as_of)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, event_type, symbol, json.dumps(payload or {}), as_of.isoformat()),
            )

    def get_calibration_stats(
        self,
        bot_id: str,
        bucket: str | None = None,
        strategy_bucket: str | None = None,
    ) -> dict[str, Any]:
        query = "SELECT stated_probability, outcome, brier_score FROM calibration_ledger WHERE bot_id = ?"
        params: list[Any] = [bot_id]
        if bucket:
            query += " AND bucket = ?"
            params.append(bucket)
        if strategy_bucket:
            query += " AND strategy_bucket = ?"
            params.append(strategy_bucket)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        if not rows:
            return {"count": 0, "mean_brier": None}
        return {
            "count": len(rows),
            "mean_brier": sum(r["brier_score"] for r in rows) / len(rows),
        }

    def start_run(
        self,
        run_id: str,
        bot_id: str,
        mode: str,
        config_hash: str,
        model_versions: dict[str, Any],
        log_path: str,
        git_sha: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        ts = (started_at or datetime.now(timezone.utc)).isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO runs
                (run_id, bot_id, mode, started_at, config_hash, model_versions_json, git_sha, log_path, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
                """,
                (
                    run_id,
                    bot_id,
                    mode,
                    ts,
                    config_hash,
                    json.dumps(model_versions),
                    git_sha,
                    log_path,
                ),
            )

    def finish_run(
        self,
        run_id: str,
        status: str = "completed",
        finished_at: datetime | None = None,
    ) -> None:
        ts = (finished_at or datetime.now(timezone.utc)).isoformat()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
                (ts, status, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "bot_id": row["bot_id"],
            "mode": row["mode"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "config_hash": row["config_hash"],
            "model_versions": json.loads(row["model_versions_json"]),
            "git_sha": row["git_sha"],
            "log_path": row["log_path"],
            "status": row["status"],
        }

    def record_decision(
        self,
        decision_id: str,
        bot_id: str,
        decision_type: str,
        action: str,
        inputs: dict[str, Any],
        mode: str,
        as_of: datetime,
        run_id: str | None = None,
        thesis_id: str | None = None,
        model_id: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        """Persist an auditable decision with the exact inputs seen at decision time."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO decisions
                (decision_id, run_id, thesis_id, bot_id, decision_type, action,
                 inputs_json, model_id, prompt_version, mode, as_of)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    run_id,
                    thesis_id,
                    bot_id,
                    decision_type,
                    action,
                    json.dumps(inputs, default=str),
                    model_id,
                    prompt_version,
                    mode,
                    as_of.isoformat(),
                ),
            )

    def get_decisions(
        self,
        bot_id: str,
        mode: str | None = None,
        decision_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load recorded decisions for a bot, ordered by time (audit/research path)."""
        query = """
            SELECT d.decision_id, d.run_id, d.thesis_id, d.bot_id, d.decision_type, d.action,
                   d.inputs_json, d.model_id, d.prompt_version, d.mode, d.as_of,
                   t.created_at AS thesis_created_at, t.strategy_bucket AS strategy_bucket
            FROM decisions d
            LEFT JOIN theses t ON t.thesis_id = d.thesis_id
            WHERE d.bot_id = ?
        """
        params: list[Any] = [bot_id]
        if mode is not None:
            query += " AND d.mode = ?"
            params.append(mode)
        if decision_type is not None:
            query += " AND d.decision_type = ?"
            params.append(decision_type)
        query += " ORDER BY d.as_of ASC, d.decision_id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "decision_id": r["decision_id"],
                "run_id": r["run_id"],
                "thesis_id": r["thesis_id"],
                "bot_id": r["bot_id"],
                "decision_type": r["decision_type"],
                "action": r["action"],
                "inputs": json.loads(r["inputs_json"]),
                "model_id": r["model_id"],
                "prompt_version": r["prompt_version"],
                "mode": r["mode"],
                "as_of": r["as_of"],
                "thesis_created_at": r["thesis_created_at"],
                "strategy_bucket": r["strategy_bucket"],
            }
            for r in rows
        ]

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Load a recorded decision for replay/audit."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        if row is None:
            return None
        return {
            "decision_id": row["decision_id"],
            "run_id": row["run_id"],
            "thesis_id": row["thesis_id"],
            "bot_id": row["bot_id"],
            "decision_type": row["decision_type"],
            "action": row["action"],
            "inputs": json.loads(row["inputs_json"]),
            "model_id": row["model_id"],
            "prompt_version": row["prompt_version"],
            "mode": row["mode"],
            "as_of": row["as_of"],
        }

    def record_flow_lens_sample(
        self,
        sample_id: str,
        logged_date: str,
        model_id: str,
        model_version: str | None,
        prompt_id: str,
        prompt_text: str,
        response_text: str,
        tickers: list[str],
        sampled_at: datetime,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO flow_lens_samples
                (sample_id, logged_date, model_id, model_version, prompt_id, prompt_text,
                 response_text, tickers_json, sampled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample_id,
                    logged_date,
                    model_id,
                    model_version,
                    prompt_id,
                    prompt_text,
                    response_text,
                    json.dumps(tickers),
                    sampled_at.isoformat(),
                ),
            )

    def get_flow_lens_samples(self, logged_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sample_id, logged_date, model_id, model_version, prompt_id,
                       prompt_text, response_text, tickers_json, sampled_at
                FROM flow_lens_samples
                WHERE logged_date = ?
                ORDER BY sampled_at ASC, sample_id ASC
                """,
                (logged_date,),
            ).fetchall()
        return [
            {
                "sample_id": r["sample_id"],
                "logged_date": r["logged_date"],
                "model_id": r["model_id"],
                "model_version": r["model_version"],
                "prompt_id": r["prompt_id"],
                "prompt_text": r["prompt_text"],
                "response_text": r["response_text"],
                "tickers": json.loads(r["tickers_json"]),
                "sampled_at": datetime.fromisoformat(r["sampled_at"]),
            }
            for r in rows
        ]

    def record_flow_lens_consensus(
        self,
        logged_date: str,
        symbol: str,
        consensus_score: float,
        novelty_flag: bool,
        model_count: int,
        prompt_count: int,
        models: list[str],
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO flow_lens_consensus
                (logged_date, symbol, consensus_score, novelty_flag, model_count,
                 prompt_count, models_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    logged_date,
                    symbol,
                    consensus_score,
                    1 if novelty_flag else 0,
                    model_count,
                    prompt_count,
                    json.dumps(models),
                ),
            )

    def get_flow_lens_consensus(self, logged_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT logged_date, symbol, consensus_score, novelty_flag, model_count,
                       prompt_count, models_json
                FROM flow_lens_consensus
                WHERE logged_date = ?
                ORDER BY consensus_score DESC, symbol ASC
                """,
                (logged_date,),
            ).fetchall()
        return [
            {
                "logged_date": r["logged_date"],
                "symbol": r["symbol"],
                "consensus_score": r["consensus_score"],
                "novelty_flag": bool(r["novelty_flag"]),
                "model_count": r["model_count"],
                "prompt_count": r["prompt_count"],
                "models": json.loads(r["models_json"]),
            }
            for r in rows
        ]

    # --- frontier edges (Phase 3a) ----------------------------------------

    def record_frontier_edge(
        self,
        *,
        edge_id: str,
        edge_type: str,
        from_symbol: str,
        counterparty_name: str,
        counterparty_symbol: str | None,
        named: bool,
        magnitude_pct: float | None,
        quoted_passage: str,
        accession_number: str,
        filing_url: str,
        form: str,
        filed_at: str,
        extracted_at: datetime,
        model_id: str | None,
        counterparty_canonical: str | None = None,
    ) -> bool:
        """Insert an edge; returns False if it already exists (idempotent re-runs).

        `counterparty_canonical` is the entity-normalized dedupe identity
        (3a-F); when omitted it falls back to the uppercased raw name.
        """
        canonical = counterparty_canonical or counterparty_name.upper()
        with self.transaction() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO frontier_edges
                    (edge_id, edge_type, from_symbol, counterparty_name, counterparty_canonical,
                     counterparty_symbol, named, magnitude_pct, quoted_passage, accession_number,
                     filing_url, form, filed_at, extracted_at, model_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        edge_type,
                        from_symbol.upper(),
                        counterparty_name,
                        canonical,
                        counterparty_symbol.upper() if counterparty_symbol else None,
                        1 if named else 0,
                        magnitude_pct,
                        quoted_passage,
                        accession_number,
                        filing_url,
                        form,
                        filed_at,
                        extracted_at.isoformat(),
                        model_id,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    @staticmethod
    def _frontier_edge_row(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "edge_id": r["edge_id"],
            "edge_type": r["edge_type"],
            "from_symbol": r["from_symbol"],
            "counterparty_name": r["counterparty_name"],
            "counterparty_canonical": r["counterparty_canonical"],
            "counterparty_symbol": r["counterparty_symbol"],
            "named": bool(r["named"]),
            "magnitude_pct": r["magnitude_pct"],
            "quoted_passage": r["quoted_passage"],
            "accession_number": r["accession_number"],
            "filing_url": r["filing_url"],
            "form": r["form"],
            "filed_at": r["filed_at"],
            "extracted_at": r["extracted_at"],
            "model_id": r["model_id"],
        }

    def get_frontier_edges(
        self,
        *,
        edge_type: str | None = None,
        from_symbol: str | None = None,
        named_only: bool = False,
    ) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []
        if edge_type is not None:
            where.append("edge_type = ?")
            params.append(edge_type)
        if from_symbol is not None:
            where.append("from_symbol = ?")
            params.append(from_symbol.upper())
        if named_only:
            where.append("named = 1")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM frontier_edges
                WHERE {' AND '.join(where)}
                ORDER BY filed_at DESC, edge_id ASC
                """,
                tuple(params),
            ).fetchall()
        return [self._frontier_edge_row(r) for r in rows]

    def get_live_frontier_edges(
        self,
        as_of: datetime,
        *,
        max_age_days: int = 548,  # ~18 months
        edge_type: str | None = None,
        named_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Edges whose source filing is recent enough to be considered live.

        Decay rule (3a): an edge is live only while its filing is <= 18 months
        old as of `as_of`. Graph queries default to named edges only —
        anonymous counterparties are stats, not edges.
        """
        cutoff = (as_of - timedelta(days=max_age_days)).date().isoformat()
        where = ["filed_at >= ?", "filed_at <= ?"]
        params: list[Any] = [cutoff, as_of.date().isoformat()]
        if edge_type is not None:
            where.append("edge_type = ?")
            params.append(edge_type)
        if named_only:
            where.append("named = 1")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM frontier_edges
                WHERE {' AND '.join(where)}
                ORDER BY filed_at DESC, edge_id ASC
                """,
                tuple(params),
            ).fetchall()
        return [self._frontier_edge_row(r) for r in rows]

    def delete_frontier_edge(self, edge_id: str) -> bool:
        """Remove one edge (guard kill). Returns True if a row was deleted."""
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM frontier_edges WHERE edge_id = ?", (edge_id,))
        return cur.rowcount > 0

    def update_frontier_edge(
        self,
        edge_id: str,
        *,
        named: bool | None = None,
        magnitude_pct: float | None = ...,  # type: ignore[assignment]
    ) -> bool:
        """Patch named/magnitude after guard demotion or G5 null-magnitude."""
        sets: list[str] = []
        params: list[Any] = []
        if named is not None:
            sets.append("named = ?")
            params.append(1 if named else 0)
        if magnitude_pct is not ...:
            sets.append("magnitude_pct = ?")
            params.append(magnitude_pct)
        if not sets:
            return False
        params.append(edge_id)
        with self.transaction() as conn:
            cur = conn.execute(
                f"UPDATE frontier_edges SET {', '.join(sets)} WHERE edge_id = ?",
                tuple(params),
            )
        return cur.rowcount > 0

    # --- Phase 5a: live-broker manual-approval queue + order-ref idempotency --

    def record_pending_approval(
        self,
        *,
        approval_id: str,
        broker: str,
        bot_id: str,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        ref_id: str,
        requested_at: datetime,
        limit_price: float | None = None,
        thesis_id: str | None = None,
        notional_est: float | None = None,
        expected_price: float | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO pending_approval_orders
                (approval_id, broker, bot_id, symbol, side, quantity, order_type,
                 limit_price, thesis_id, ref_id, notional_est, expected_price, status, requested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    approval_id, broker, bot_id, symbol, side, quantity, order_type,
                    limit_price, thesis_id, ref_id, notional_est, expected_price,
                    requested_at.isoformat(),
                ),
            )

    def list_pending_approvals(self, status: str = "pending") -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_approval_orders
                WHERE status = ?
                ORDER BY requested_at ASC, approval_id ASC
                """,
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approval_orders WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return dict(row) if row else None

    def decide_pending_approval(
        self,
        approval_id: str,
        status: str,
        *,
        decided_at: datetime | None = None,
        note: str | None = None,
        venue_order_id: str | None = None,
    ) -> None:
        if status not in {"approved", "rejected", "placed", "failed"}:
            raise ValueError(f"invalid approval status: {status}")
        ts = (decided_at or datetime.now(timezone.utc)).isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE pending_approval_orders
                SET status = ?, decided_at = ?, note = COALESCE(?, note),
                    venue_order_id = COALESCE(?, venue_order_id)
                WHERE approval_id = ?
                """,
                (status, ts, note, venue_order_id, approval_id),
            )

    def record_order_ref(
        self,
        *,
        ref_id: str,
        broker: str,
        bot_id: str,
        symbol: str,
        side: str,
        quantity: float | None = None,
        thesis_id: str | None = None,
        expected_price: float | None = None,
    ) -> None:
        """Persist the idempotency key BEFORE the venue call (crash-safe).

        `expected_price` (shot 12) is the decision-time mark carried onto the
        order so live-fill reconciliation can compute realized-vs-expected
        slippage per order without a fragile join back to the decision row.
        """
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO broker_order_refs
                (ref_id, broker, bot_id, symbol, side, quantity, thesis_id,
                 expected_price, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created')
                """,
                (ref_id, broker, bot_id, symbol, side, quantity, thesis_id, expected_price),
            )
            # An order ref may be pre-created (mint) without the expected price
            # and later re-recorded by the approval CLI with it (or vice versa);
            # never clobber a known price with NULL.
            if expected_price is not None:
                conn.execute(
                    "UPDATE broker_order_refs SET expected_price = ? WHERE ref_id = ?",
                    (expected_price, ref_id),
                )

    def update_order_ref(
        self, ref_id: str, *, state: str, venue_order_id: str | None = None
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE broker_order_refs
                SET state = ?, venue_order_id = COALESCE(?, venue_order_id),
                    updated_at = datetime('now')
                WHERE ref_id = ?
                """,
                (state, venue_order_id, ref_id),
            )

    def get_order_ref(self, ref_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM broker_order_refs WHERE ref_id = ?", (ref_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_order_refs(
        self,
        *,
        broker: str | None = None,
        states: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Order refs, optionally filtered by broker and/or a set of states.

        Used by the shot-12 fill reconciler to find the live orders a venue fill
        might belong to (matched by ``venue_order_id`` first, then ``ref_id``).
        """
        where = ["1=1"]
        params: list[Any] = []
        if broker is not None:
            where.append("broker = ?")
            params.append(broker)
        if states:
            placeholders = ",".join("?" for _ in states)
            where.append(f"state IN ({placeholders})")
            params.extend(states)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM broker_order_refs
                WHERE {' AND '.join(where)}
                ORDER BY created_at ASC, ref_id ASC
                """,
                tuple(params),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_fill(self, fill_id: str) -> dict[str, Any] | None:
        """One fill by id, or None — lets the reconciler prove idempotency
        (a fill already on disk is left untouched on a re-run)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM fills WHERE fill_id = ?", (fill_id,)).fetchone()
        return dict(row) if row else None

    def get_flow_lens_consensus_symbols(
        self,
        logged_date: str,
        *,
        min_score: float = 0.5,
    ) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol
                FROM flow_lens_consensus
                WHERE logged_date = ? AND consensus_score >= ?
                ORDER BY symbol ASC
                """,
                (logged_date, min_score),
            ).fetchall()
        return [str(r["symbol"]) for r in rows]

    def get_flow_lens_consensus_range(
        self,
        start_date: str,
        end_date: str,
        *,
        min_score: float = 0.0,
        novelty_only: bool = False,
    ) -> list[dict[str, Any]]:
        where = "logged_date >= ? AND logged_date <= ? AND consensus_score >= ?"
        params: list[Any] = [start_date, end_date, min_score]
        if novelty_only:
            where += " AND novelty_flag = 1"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT logged_date, symbol, consensus_score, novelty_flag, model_count,
                       prompt_count, models_json
                FROM flow_lens_consensus
                WHERE {where}
                ORDER BY logged_date ASC, consensus_score DESC, symbol ASC
                """,
                tuple(params),
            ).fetchall()
        return [
            {
                "logged_date": r["logged_date"],
                "symbol": r["symbol"],
                "consensus_score": r["consensus_score"],
                "novelty_flag": bool(r["novelty_flag"]),
                "model_count": r["model_count"],
                "prompt_count": r["prompt_count"],
                "models": json.loads(r["models_json"]),
            }
            for r in rows
        ]

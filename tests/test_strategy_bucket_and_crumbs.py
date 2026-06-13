"""Tests for the strategy_bucket schema (synthesis ticket) and the crumbs scanner (F4)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from stockbot.core.calibration.ledger import CalibrationLedger
from stockbot.core.state.store import StateStore
from stockbot.data.real_loader import CachedHTTP, cache_file_path
from stockbot.tools.crumbs.scanner import EDGAR_FTS_URL, format_alerts, scan_odd_lot_tenders


# --- strategy_bucket -------------------------------------------------------


def test_strategy_bucket_recorded_and_gated(tmp_path: Path):
    store = StateStore(str(tmp_path / "bucket.db"))
    ledger = CalibrationLedger(store)

    bucket = ledger.strategy_bucket("earnings", "ride")
    assert bucket == "earnings:ride"

    for i in range(12):
        tid = f"t{i}"
        ledger.record_prediction(
            thesis_id=tid,
            bot_id="proportionality",
            symbol="FAKE",
            direction="ride",
            stated_probability=0.7,
            trade_type="earnings",
            mode="paper",
            strategy_bucket=bucket,
        )
        ledger.resolve(
            thesis_id=tid,
            bot_id="proportionality",
            trade_type="earnings",
            stated_probability=0.7,
            outcome=(i % 10) < 7,  # 70% hit rate at stated 0.7 -> calibrated
            bucket="high_confidence",
            strategy_bucket=bucket,
        )

    stats = store.get_calibration_stats("proportionality", strategy_bucket=bucket)
    assert stats["count"] == 12
    assert ledger.is_strategy_bucket_calibrated("proportionality", bucket)
    # A different bucket has no samples -> not calibrated -> no capital.
    assert not ledger.is_strategy_bucket_calibrated("proportionality", "crash:fade")


def test_strategy_bucket_migration_on_existing_db(tmp_path: Path):
    """An old DB without strategy_bucket columns is migrated in place, data intact."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE theses (
            thesis_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, stated_probability REAL NOT NULL, rationale TEXT,
            mode TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT, outcome INTEGER
        );
        CREATE TABLE calibration_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, thesis_id TEXT NOT NULL,
            bot_id TEXT NOT NULL, trade_type TEXT NOT NULL, stated_probability REAL NOT NULL,
            outcome INTEGER NOT NULL, brier_score REAL NOT NULL, bucket TEXT NOT NULL,
            resolved_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO theses VALUES ('legacy', 'bot', 'X', 'buy', 0.6, NULL, 'paper', '2026-01-01', NULL, NULL);
        """
    )
    conn.commit()
    conn.close()

    store = StateStore(str(db))  # triggers migration
    with store.transaction() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(theses)")}
        assert "strategy_bucket" in cols
        row = conn.execute("SELECT strategy_bucket FROM theses WHERE thesis_id='legacy'").fetchone()
        assert row["strategy_bucket"] == ""  # legacy rows default, not lost

    # New writes carry the bucket.
    store.record_thesis(
        thesis_id="new",
        bot_id="bot",
        symbol="X",
        direction="fade",
        stated_probability=0.6,
        mode="paper",
        strategy_bucket="crash:fade",
    )
    with store.transaction() as conn:
        row = conn.execute("SELECT strategy_bucket FROM theses WHERE thesis_id='new'").fetchone()
        assert row["strategy_bucket"] == "crash:fade"


# --- crumbs scanner (F4) ---------------------------------------------------


def _seed_fts_fixture(cache_dir: Path, form: str, hits: list[dict]) -> None:
    params = {
        "q": '"odd lot"',
        "forms": form,
        "dateRange": "custom",
        "startdt": "2026-05-12",
        "enddt": "2026-06-11",
    }
    payload = {"hits": {"hits": hits}}
    cache_file_path(cache_dir, EDGAR_FTS_URL, params).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_crumbs_scanner_catches_odd_lot_tender_offline(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Modeled on the real EDGAR FTS response shape; backfill-style fixture.
    _seed_fts_fixture(
        cache_dir,
        "SC TO-I",
        [
            {
                "_id": "0001234567-26-000042:scto.htm",
                "_source": {
                    "adsh": "0001234567-26-000042",
                    "ciks": ["0001234567"],
                    "display_names": ["Example Holdings Inc (EXMP)"],
                    "file_type": "SC TO-I",
                    "file_date": "2026-06-02",
                    "file_description": "Issuer tender offer: holders of 99 or fewer shares"
                    " (odd lot holders) will not be subject to proration",
                },
            }
        ],
    )
    _seed_fts_fixture(cache_dir, "SC 13E4", [])
    _seed_fts_fixture(cache_dir, "SC TO-T", [])

    client = CachedHTTP(cache_dir=cache_dir, offline=True)
    alerts = scan_odd_lot_tenders(
        client,
        "Stockbot-Test/0.1 (test@example.com)",
        lookback_days=30,
        today=date(2026, 6, 11),
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.accession_number == "0001234567-26-000042"
    assert alert.company == "Example Holdings Inc (EXMP)"
    assert alert.form == "SC TO-I"
    assert alert.filed_at == "2026-06-02"
    assert "odd lot" in alert.matched_phrase.lower() or "99 or fewer" in alert.matched_phrase.lower()
    assert alert.filing_url.startswith("https://www.sec.gov/")

    text = format_alerts(alerts)
    assert "Example Holdings" in text
    assert "verify by hand" in text


def test_crumbs_scanner_dedupes_and_handles_empty(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    dup_hit = {
        "_id": "0009999999-26-000001:doc.htm",
        "_source": {
            "adsh": "0009999999-26-000001",
            "ciks": ["0009999999"],
            "display_names": ["Dup Corp"],
            "file_type": "SC TO-I",
            "file_date": "2026-05-20",
            "file_description": "odd-lot priority tender",
        },
    }
    _seed_fts_fixture(cache_dir, "SC TO-I", [dup_hit])
    _seed_fts_fixture(cache_dir, "SC 13E4", [dup_hit])  # same accession via second form
    _seed_fts_fixture(cache_dir, "SC TO-T", [])

    client = CachedHTTP(cache_dir=cache_dir, offline=True)
    alerts = scan_odd_lot_tenders(
        client,
        "Stockbot-Test/0.1 (test@example.com)",
        lookback_days=30,
        today=date(2026, 6, 11),
    )
    assert len(alerts) == 1  # deduped by accession number

    assert "no odd-lot tender candidates" in format_alerts([])

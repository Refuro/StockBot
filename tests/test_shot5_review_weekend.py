"""Shot-5 review fix 5-R1 — non-trading-day handling.

The scheduled tick fires daily including weekends/holidays, but on a
non-trading day Tiingo IEX still serves the LAST session, so an unguarded
tick (a) settles Friday's next-bar orders at Friday's own price and
(b) re-detects Friday's crashes under fresh event_ids (fresh LLM spend,
and a vetoed fade gets a fresh roll of the dice). Guards under test:
- `is_us_trading_day` (weekends + 2026/2027 NYSE holidays, fail-open beyond)
- `paper_run.main` skips non-trading days (override: --force)
- `LiveCrashDetector` drops rows whose session timestamp is not today's
  (also covers trading halts on a normal weekday).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from stockbot.bots.proportionality.crash import LiveCrashDetector, TIINGO_IEX_URL
from stockbot.data.real_loader import CachedHTTP, cache_file_path
from stockbot.harness import paper_run
from stockbot.harness.trading_calendar import is_us_trading_day

UTC = timezone.utc
# 2026-06-12 is a Friday; 13/14 the first weekend of the live race.
FRIDAY = datetime(2026, 6, 12, 22, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 6, 13, 22, 0, tzinfo=UTC)


class TestTradingCalendar:
    def test_weekdays_trade(self) -> None:
        assert is_us_trading_day(date(2026, 6, 12))  # Friday
        assert is_us_trading_day(date(2026, 6, 15))  # Monday

    def test_weekends_do_not(self) -> None:
        assert not is_us_trading_day(date(2026, 6, 13))
        assert not is_us_trading_day(date(2026, 6, 14))

    def test_holidays_do_not(self) -> None:
        assert not is_us_trading_day(date(2026, 6, 19))   # Juneteenth (shadow week!)
        assert not is_us_trading_day(date(2026, 7, 3))    # Jul 4 observed
        assert not is_us_trading_day(date(2026, 11, 26))  # Thanksgiving
        assert not is_us_trading_day(date(2027, 6, 18))   # Juneteenth observed

    def test_unknown_year_fails_open_on_weekdays(self) -> None:
        # 2030-01-01 is beyond the table AND a real holiday — the table being
        # unmaintained must degrade to the OLD behavior (tick), never skip
        # actual trading days. Weekends still skip.
        assert is_us_trading_day(date(2030, 1, 1))
        assert not is_us_trading_day(date(2030, 1, 5))  # Saturday


class TestMainSkipsNonTradingDays:
    def _freeze(self, monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
        class _Clock:
            @staticmethod
            def now(tz=None):  # noqa: ANN001
                return now if tz is None else now.astimezone(tz)

        monkeypatch.setattr(paper_run, "datetime", _Clock)

    def test_saturday_tick_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._freeze(monkeypatch, SATURDAY)

        def _boom(**_kwargs):
            raise AssertionError("run_paper_tick must not run on a non-trading day")

        monkeypatch.setattr(paper_run, "run_paper_tick", _boom)
        assert paper_run.main([]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out == {"skipped": "non_trading_day", "date": "2026-06-13"}

    def test_force_overrides_the_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._freeze(monkeypatch, SATURDAY)
        called: dict = {}

        def _capture(**kwargs):
            called.update(kwargs)
            return {"lanes": {}}

        monkeypatch.setattr(paper_run, "run_paper_tick", _capture)
        assert paper_run.main(["--force"]) == 0
        assert called  # ran despite Saturday

    def test_friday_tick_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._freeze(monkeypatch, FRIDAY)
        called: dict = {}

        def _capture(**kwargs):
            called.update(kwargs)
            return {"lanes": {}}

        monkeypatch.setattr(paper_run, "run_paper_tick", _capture)
        assert paper_run.main([]) == 0
        assert called


class TestDetectorStaleSessionGuard:
    _BAND = {"DOWN": {"market_cap": 1e9, "median_dollar_volume": 2e6}}

    def _detector(self, tmp_path: Path, rows: list[dict]) -> LiveCrashDetector:
        client = CachedHTTP(cache_dir=tmp_path, offline=True)
        tickers = ",".join(sorted({r["ticker"] for r in rows}))
        cache_file_path(
            tmp_path, TIINGO_IEX_URL, {"tickers": tickers, "token": "k"}
        ).write_text(json.dumps(rows), encoding="utf-8")
        return LiveCrashDetector(
            band=self._BAND, client=client, tiingo_api_key="k", sec_user_agent="t t@e.com"
        )

    def test_friday_session_not_redetected_on_saturday(self, tmp_path: Path) -> None:
        # Saturday's IEX row still describes Friday's session.
        det = self._detector(
            tmp_path,
            [{
                "ticker": "DOWN", "prevClose": 50.0, "tngoLast": 42.0,
                "lastSaleTimeStamp": "2026-06-12T20:00:00+00:00",
            }],
        )
        assert det.detect(SATURDAY) == []

    def test_same_day_session_detected(self, tmp_path: Path) -> None:
        det = self._detector(
            tmp_path,
            [{
                "ticker": "DOWN", "prevClose": 50.0, "tngoLast": 42.0,
                "lastSaleTimeStamp": "2026-06-12T20:00:00+00:00",
            }],
        )
        crashes = det.detect(FRIDAY)
        assert [c.symbol for c in crashes] == ["DOWN"]

    def test_halted_name_not_redetected_on_a_weekday(self, tmp_path: Path) -> None:
        # A name halted since its Friday crash keeps serving the stale session
        # on Monday — the guard drops it on normal weekdays too.
        det = self._detector(
            tmp_path,
            [{
                "ticker": "DOWN", "prevClose": 50.0, "tngoLast": 42.0,
                "timestamp": "2026-06-12T20:00:00Z",
            }],
        )
        assert det.detect(datetime(2026, 6, 15, 22, 0, tzinfo=UTC)) == []

    def test_missing_timestamp_fails_open(self, tmp_path: Path) -> None:
        # No timestamp field: keep the crash (old behavior) rather than
        # silently blinding the detector to a data-shape change.
        det = self._detector(
            tmp_path,
            [{"ticker": "DOWN", "prevClose": 50.0, "tngoLast": 42.0}],
        )
        crashes = det.detect(SATURDAY)
        assert [c.symbol for c in crashes] == ["DOWN"]

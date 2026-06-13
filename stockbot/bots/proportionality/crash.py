"""Ticket 2.5a — mechanical crash detection + primary-source-document retrieval.

The fade side of the Proportionality Engine starts here, and this module is
deliberately LLM-free: a crash is a close-to-close drop >= 10% on a name in
the $300M-$10B / >$1M-ADV band, and the "source document" is whatever EDGAR
filing plausibly caused it — fetched as a document reference, never a
headline. The proportionality rubric (2.5b) judges; this module only detects
and retrieves.

Band coverage caveat (documented honestly, per the brief): the expanded
universe was screened at $300M-$3B market cap and >=$500k median dollar ADV
(see ``universe_expanded.metadata.json``). The 2.5a band is $300M-$10B with
>$1M ADV, so:
- the $3B-$10B slice of the band has NO coverage (no symbols in the universe);
- within the universe, symbols with median ADV in [$500k, $1M] are excluded
  by the band filter here;
- market cap / ADV are as measured at screen time (2026-06), not point-in-time.

Source-document selection rule (fixed before the labeled sample was run):
- candidate filings are those filed within [crash_date - 3 days,
  crash_date + 1 day] (8-Ks formalizing a press release often land the next
  morning; the wave that caused a close-to-close drop typically starts within
  the prior 1-2 sessions);
- among candidates, prefer the highest-priority form class (current reports >
  offerings/dilution > periodic results > tender/going-private > other), and
  within a class the filing date closest to the crash date (ties: later date);
- crashes with no EDGAR filing in the window (analyst downgrades, court
  orders, sector contagion...) get ``source_doc=None`` — the 2.5b gate skips
  them rather than grading a headline.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from stockbot.data.real_loader import SEC_SUBMISSIONS_URL, CachedHTTP

logger = logging.getLogger(__name__)

# --- pre-registered mechanical constants (2.5a) -------------------------------
CRASH_DROP_PCT = 0.10                 # close-to-close drop >= 10%
BAND_MIN_MARKET_CAP = 300e6
BAND_MAX_MARKET_CAP = 10e9
BAND_MIN_DOLLAR_ADV = 1_000_000.0
SOURCE_DOC_LOOKBACK_DAYS = 3
SOURCE_DOC_LOOKAHEAD_DAYS = 1

TIINGO_IEX_URL = "https://api.tiingo.com/iex/"
_IEX_BATCH_SIZE = 50

# Primary-source form classes, highest priority first. Lower index wins.
_FORM_CLASSES: tuple[tuple[str, ...], ...] = (
    ("8-K", "8-K/A", "6-K", "6-K/A"),                     # current reports
    ("424B1", "424B2", "424B3", "424B4", "424B5", "424B7", "424B8",
     "S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-1/A", "F-3", "F-3/A"),  # offerings/dilution
    ("10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F"),    # periodic results
    ("SC TO-I", "SC TO-T", "SC 13E3", "SC 14D9"),         # tender / going-private
)


def _form_priority(form: str) -> int:
    form = form.strip().upper()
    for i, cls in enumerate(_FORM_CLASSES):
        if form in cls:
            return i
    return len(_FORM_CLASSES)


@dataclass(frozen=True)
class SourceDoc:
    """Reference to the EDGAR filing judged to be the crash's primary source."""

    form: str
    accession_number: str
    filed_at: str  # YYYY-MM-DD
    primary_document: str
    cik: int

    @property
    def url(self) -> str:
        return (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{self.cik}/{self.accession_number.replace('-', '')}/{self.primary_document}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "form": self.form,
            "accession_number": self.accession_number,
            "filed_at": self.filed_at,
            "primary_document": self.primary_document,
            "cik": self.cik,
            "url": self.url,
        }


@dataclass(frozen=True)
class CrashEvent:
    """A typed mechanical crash detection with its source-document reference."""

    event_id: str            # "{symbol}-crash-{YYYY-MM-DD}"
    symbol: str
    as_of: datetime          # timestamp of the crash bar (the lower close)
    prev_close: float
    close: float
    drop_pct: float          # negative fraction, e.g. -0.134
    market_cap: float | None = None
    median_dollar_volume: float | None = None
    source_doc: SourceDoc | None = None
    payload: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


# ---------------------------------------------------------------------------
# Band filter (universe metadata)
# ---------------------------------------------------------------------------


def load_band_symbols(metadata_path: str | Path) -> dict[str, dict[str, float]]:
    """Symbols passing the 2.5a band, from the universe screener's metadata.

    Returns {symbol: {"market_cap": ..., "median_dollar_volume": ...}} for
    names with market cap in [$300M, $10B] and median dollar ADV > $1M.
    Values are screen-time measurements (see module docstring caveat).
    """
    meta = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for row in meta.get("symbols", []):
        cap = float(row.get("market_cap") or 0.0)
        adv = float(row.get("median_dollar_volume") or 0.0)
        if BAND_MIN_MARKET_CAP <= cap <= BAND_MAX_MARKET_CAP and adv > BAND_MIN_DOLLAR_ADV:
            out[str(row["symbol"]).upper()] = {
                "market_cap": cap,
                "median_dollar_volume": adv,
            }
    return out


# ---------------------------------------------------------------------------
# Detection (pure mechanics)
# ---------------------------------------------------------------------------


def detect_crashes_in_series(
    series: Iterable[tuple[datetime, float]],
    *,
    min_drop_pct: float = CRASH_DROP_PCT,
) -> list[tuple[datetime, float, float, float]]:
    """(crash_ts, prev_close, close, drop_pct) for every close-to-close drop.

    `series` must be chronologically sorted (ts, close) pairs; non-positive
    closes are skipped defensively (bad rows must not fabricate drops).
    """
    out: list[tuple[datetime, float, float, float]] = []
    prev: tuple[datetime, float] | None = None
    for ts, close in series:
        if close <= 0:
            prev = None
            continue
        if prev is not None:
            drop = (close - prev[1]) / prev[1]
            if drop <= -min_drop_pct:
                out.append((ts, prev[1], close, drop))
        prev = (ts, close)
    return out


def detect_crashes_from_prices_csv(
    prices_csv: str | Path,
    band: dict[str, dict[str, float]],
    *,
    min_drop_pct: float = CRASH_DROP_PCT,
) -> list[CrashEvent]:
    """All band-filtered crashes in a historical prices.csv (no source docs).

    Source-document references are attached separately (network/cached step);
    this function is pure CSV mechanics so tests can run offline.
    """
    series: dict[str, list[tuple[datetime, float]]] = {}
    with Path(prices_csv).open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = str(row["symbol"]).upper()
            if symbol not in band:
                continue
            series.setdefault(symbol, []).append(
                (datetime.fromisoformat(row["timestamp"]), float(row["price"]))
            )
    out: list[CrashEvent] = []
    for symbol, rows in series.items():
        rows.sort(key=lambda x: x[0])
        for ts, prev_close, close, drop in detect_crashes_in_series(
            rows, min_drop_pct=min_drop_pct
        ):
            out.append(
                CrashEvent(
                    event_id=f"{symbol}-crash-{ts.date().isoformat()}",
                    symbol=symbol,
                    as_of=ts,
                    prev_close=prev_close,
                    close=close,
                    drop_pct=drop,
                    market_cap=band[symbol]["market_cap"],
                    median_dollar_volume=band[symbol]["median_dollar_volume"],
                )
            )
    out.sort(key=lambda e: (e.as_of, e.symbol))
    return out


# ---------------------------------------------------------------------------
# Primary source document (EDGAR submissions, cached)
# ---------------------------------------------------------------------------


def find_crash_source_doc(
    client: CachedHTTP,
    *,
    cik: int,
    crash_date: date,
    sec_user_agent: str,
) -> SourceDoc | None:
    """The filing most plausibly behind a crash on `crash_date` (rule in module doc)."""
    raw = client.get_json(
        SEC_SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": sec_user_agent}
    )
    recent = raw.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    lo = crash_date - timedelta(days=SOURCE_DOC_LOOKBACK_DAYS)
    hi = crash_date + timedelta(days=SOURCE_DOC_LOOKAHEAD_DAYS)
    best: SourceDoc | None = None
    best_key: tuple[int, int, str] | None = None
    for i, form in enumerate(forms):
        try:
            fd = date.fromisoformat(str(filing_dates[i]))
        except (ValueError, IndexError):
            continue
        if fd < lo or fd > hi:
            continue
        doc = str(primary_docs[i]) if i < len(primary_docs) and primary_docs[i] else ""
        if not doc:
            continue
        # Sort key: form class, distance from crash date, then later date wins
        # (inverted ISO date string so min() prefers the later of equal-distance days).
        key = (
            _form_priority(str(form)),
            abs((fd - crash_date).days),
            "".join(chr(255 - ord(c)) for c in fd.isoformat()),
        )
        if best_key is None or key < best_key:
            best_key = key
            best = SourceDoc(
                form=str(form),
                accession_number=str(accessions[i]),
                filed_at=fd.isoformat(),
                primary_document=doc,
                cik=cik,
            )
    return best


def attach_source_docs(
    crashes: list[CrashEvent],
    client: CachedHTTP,
    ticker_map: dict[str, int],
    sec_user_agent: str,
) -> list[CrashEvent]:
    """Return crashes with source-document references attached (None kept honest)."""
    out: list[CrashEvent] = []
    for ev in crashes:
        cik = ticker_map.get(ev.symbol)
        doc: SourceDoc | None = None
        if cik is not None:
            try:
                doc = find_crash_source_doc(
                    client, cik=cik, crash_date=ev.as_of.date(), sec_user_agent=sec_user_agent
                )
            except Exception as exc:  # noqa: BLE001 - per-crash isolation
                logger.warning("source-doc lookup failed for %s: %s", ev.event_id, exc)
        payload = dict(ev.payload)
        payload["cik"] = cik
        if doc is not None:
            payload["source_doc"] = doc.to_dict()
        out.append(
            CrashEvent(
                event_id=ev.event_id,
                symbol=ev.symbol,
                as_of=ev.as_of,
                prev_close=ev.prev_close,
                close=ev.close,
                drop_pct=ev.drop_pct,
                market_cap=ev.market_cap,
                median_dollar_volume=ev.median_dollar_volume,
                source_doc=doc,
                payload=payload,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Live detection (daily tick) — batched Tiingo IEX, per-day cached
# ---------------------------------------------------------------------------


class LiveCrashDetector:
    """Detects today's close-to-close crashes for the daily paper tick.

    One batched Tiingo IEX request per ~50 names reads `prevClose` and the
    latest price; band filtering uses the universe screener's metadata. The
    result is computed once per (process, day) and cached, so multiple lanes
    sharing this instance see the identical crash list at zero extra cost —
    the same pattern as the 2c quality gate's per-event cache.
    """

    def __init__(
        self,
        *,
        band: dict[str, dict[str, float]],
        client: CachedHTTP,
        tiingo_api_key: str,
        sec_user_agent: str,
        ticker_map: dict[str, int] | None = None,
        min_drop_pct: float = CRASH_DROP_PCT,
    ) -> None:
        self._band = band
        self._client = client
        self._tiingo_key = tiingo_api_key
        self._sec_user_agent = sec_user_agent
        self._ticker_map = ticker_map
        self._min_drop = min_drop_pct
        self._cache: dict[str, list[CrashEvent]] = {}  # day-iso -> crashes

    def detect(self, now: datetime) -> list[CrashEvent]:
        day = now.date().isoformat()
        if day in self._cache:
            return self._cache[day]
        crashes: list[CrashEvent] = []
        symbols = sorted(self._band)
        for offset in range(0, len(symbols), _IEX_BATCH_SIZE):
            chunk = symbols[offset : offset + _IEX_BATCH_SIZE]
            try:
                rows = self._client.get_json(
                    TIINGO_IEX_URL,
                    params={"tickers": ",".join(chunk), "token": self._tiingo_key},
                )
            except Exception as exc:  # noqa: BLE001 - per-batch isolation
                logger.warning("IEX crash batch failed (%s..): %s", chunk[0], exc)
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                symbol = str(row.get("ticker", "")).upper()
                prev_close = row.get("prevClose")
                last = row.get("tngoLast") or row.get("last")
                if not symbol or not prev_close or not last:
                    continue
                prev_f, last_f = float(prev_close), float(last)
                if prev_f <= 0 or last_f <= 0:
                    continue
                # 5-R1: only today's session counts. A stale row (weekend,
                # holiday, trading halt) still describes the LAST session, and
                # re-emitting that crash under a fresh event_id would re-grade
                # it — and give a vetoed fade a fresh roll of the dice. Rows
                # without a parseable timestamp fall through (fail-open).
                ts_raw = row.get("lastSaleTimeStamp") or row.get("timestamp")
                if ts_raw:
                    try:
                        ts_day = datetime.fromisoformat(
                            str(ts_raw).replace("Z", "+00:00")
                        ).date().isoformat()
                    except ValueError:
                        ts_day = None
                    if ts_day is not None and ts_day != day:
                        continue
                drop = (last_f - prev_f) / prev_f
                if drop > -self._min_drop:
                    continue
                info = self._band.get(symbol, {})
                crashes.append(
                    CrashEvent(
                        event_id=f"{symbol}-crash-{day}",
                        symbol=symbol,
                        as_of=now,
                        prev_close=prev_f,
                        close=last_f,
                        drop_pct=drop,
                        market_cap=info.get("market_cap"),
                        median_dollar_volume=info.get("median_dollar_volume"),
                    )
                )
        if crashes and self._ticker_map is not None:
            crashes = attach_source_docs(
                crashes, self._client, self._ticker_map, self._sec_user_agent
            )
        crashes.sort(key=lambda e: e.symbol)
        self._cache[day] = crashes
        if crashes:
            logger.info(
                "live crash detector: %d crash(es) on %s: %s",
                len(crashes), day, ", ".join(f"{c.symbol} {c.drop_pct:+.1%}" for c in crashes),
            )
        return crashes

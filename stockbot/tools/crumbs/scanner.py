"""EDGAR full-text scan for odd-lot tender offers (Workstream F4).

Queries the EDGAR full-text search API (efts.sec.gov) for SC TO-I / SC 13E4
filings mentioning odd-lot provisions, and emits alert dicts with the filing
metadata and a link. Reuses `CachedHTTP` so tests run offline from fixtures.

v1 is deliberately regex/keyword-first (no LLM): the acceptance bar is catching
the filing and surfacing it with enough metadata to read by hand. Haiku-based
clause extraction (tender price, deadline, proration, withdrawal terms) can be
layered on later behind the same alert shape.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from stockbot.data.real_loader import CachedHTTP

logger = logging.getLogger(__name__)

EDGAR_FTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Tender forms that can carry odd-lot priority clauses.
TENDER_FORMS = ("SC TO-I", "SC 13E4", "SC TO-T")

# Phrases that indicate an odd-lot priority provision (case-insensitive).
ODD_LOT_PATTERNS = (
    re.compile(r"odd[\s-]?lot", re.IGNORECASE),
    re.compile(r"fewer than 100 shares", re.IGNORECASE),
    re.compile(r"99 or fewer shares", re.IGNORECASE),
)


@dataclass(frozen=True)
class CrumbAlert:
    accession_number: str
    form: str
    company: str
    cik: str
    filed_at: str
    matched_phrase: str
    filing_url: str
    source: str = "edgar_fts_odd_lot"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_match(text: str) -> str | None:
    for pattern in ODD_LOT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def _filing_url(cik: str, adsh: str) -> str:
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik}&type=SC+TO&dateb=&owner=include&count=10"
        f"&search_text={adsh}"
    )


def _parse_hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload.get("hits", {}).get("hits", [])


def scan_odd_lot_tenders(
    client: CachedHTTP,
    sec_user_agent: str,
    *,
    lookback_days: int = 30,
    today: date | None = None,
) -> list[CrumbAlert]:
    """Scan recent tender filings for odd-lot provisions. One FTS call per form."""
    end = today or date.today()
    start = end - timedelta(days=lookback_days)
    alerts: list[CrumbAlert] = []
    seen: set[str] = set()
    failed_forms: list[str] = []

    for form in TENDER_FORMS:
        try:
            payload = client.get_json(
                EDGAR_FTS_URL,
                params={
                    "q": '"odd lot"',
                    "forms": form,
                    "dateRange": "custom",
                    "startdt": start.isoformat(),
                    "enddt": end.isoformat(),
                },
                headers={"User-Agent": sec_user_agent},
            )
        except httpx.HTTPError as exc:
            # EDGAR FTS intermittently 500s on some single-form filters
            # (observed for SC 13E4 / SC TO-T). One bad form must not kill
            # the daily scan — warn loudly and keep going.
            logger.warning("EDGAR FTS query failed for form %r: %s", form, exc)
            failed_forms.append(form)
            continue
        for hit in _parse_hits(payload):
            source = hit.get("_source", {})
            adsh = str(source.get("adsh", ""))
            if not adsh or adsh in seen:
                continue
            seen.add(adsh)

            # FTS returns the matched text region; fall back to abstract fields.
            text_blob = " ".join(
                str(x)
                for x in (
                    hit.get("highlight", {}),
                    source.get("display_names", ""),
                    source.get("file_description", ""),
                )
            )
            matched = _first_match(text_blob) or "odd lot (FTS query match)"

            ciks = source.get("ciks") or [""]
            names = source.get("display_names") or ["(unknown company)"]
            cik = str(ciks[0]).lstrip("0") or str(ciks[0])
            alerts.append(
                CrumbAlert(
                    accession_number=adsh,
                    form=str(source.get("file_type", form)),
                    company=str(names[0]),
                    cik=cik,
                    filed_at=str(source.get("file_date", "")),
                    matched_phrase=matched,
                    filing_url=_filing_url(cik, adsh),
                )
            )

    if failed_forms and len(failed_forms) == len(TENDER_FORMS):
        raise RuntimeError(f"all EDGAR FTS queries failed: {failed_forms}")

    alerts.sort(key=lambda a: a.filed_at, reverse=True)
    return alerts


def format_alerts(alerts: list[CrumbAlert]) -> str:
    """Human-readable notification block (printed / logged / piped to notifier)."""
    if not alerts:
        return "crumbs: no odd-lot tender candidates in window."
    lines = [f"crumbs: {len(alerts)} odd-lot tender candidate(s):"]
    for a in alerts:
        lines.append(
            f"  [{a.filed_at}] {a.company} — {a.form} {a.accession_number}"
            f" (matched: '{a.matched_phrase}')\n    {a.filing_url}"
        )
    lines.append(
        "  NOTE: verify by hand before acting — tender price, expiration, proration"
        " terms, and withdrawal conditions. Max 99 shares; manual order by design."
    )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = CachedHTTP(cache_dir=os.environ.get("CRUMBS_CACHE_DIR", "data/crumbs_cache"))
    agent = os.environ.get("SEC_USER_AGENT", "Stockbot/0.1 (contact@example.com)")
    alerts = scan_odd_lot_tenders(client, agent)
    print(format_alerts(alerts))
    out = os.environ.get("CRUMBS_OUT_JSON")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump([a.to_dict() for a in alerts], f, indent=2)


if __name__ == "__main__":  # pragma: no cover
    main()

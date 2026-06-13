"""EDGAR 10-K retrieval for the Frontier graph builder (Phase 3a).

Fetches the most recent 10-K (or 10-K/A) per company through `CachedHTTP`
(same submissions/archives endpoints the real loader uses), and converts the
primary HTML document to plain text for the regex candidate pass.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Any

from stockbot.data.real_loader import SEC_SUBMISSIONS_URL, CachedHTTP

ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{document}"

_ANNUAL_FORMS = ("10-K", "10-K/A")


@dataclass(frozen=True)
class FilingRef:
    symbol: str
    cik: int
    form: str
    accession_number: str
    filed_at: str  # YYYY-MM-DD
    primary_document: str

    @property
    def url(self) -> str:
        return ARCHIVES_URL.format(
            cik=self.cik,
            accession_nodash=self.accession_number.replace("-", ""),
            document=self.primary_document,
        )


def latest_annual_filing(
    client: CachedHTTP,
    symbol: str,
    cik: int,
    sec_user_agent: str,
) -> FilingRef | None:
    """Most recent 10-K / 10-K/A from the company's submissions index."""
    raw = client.get_json(
        SEC_SUBMISSIONS_URL.format(cik=cik),
        headers={"User-Agent": sec_user_agent},
    )
    recent: dict[str, Any] = raw.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])

    best: FilingRef | None = None
    for i, form in enumerate(forms):
        if str(form) not in _ANNUAL_FORMS:
            continue
        doc = str(docs[i]) if i < len(docs) and docs[i] else ""
        if not doc:
            continue
        ref = FilingRef(
            symbol=symbol.upper(),
            cik=cik,
            form=str(form),
            accession_number=str(accessions[i]),
            filed_at=str(dates[i]),
            primary_document=doc,
        )
        if best is None or ref.filed_at > best.filed_at:
            best = ref
    return best


def fetch_filing_text(client: CachedHTTP, ref: FilingRef, sec_user_agent: str) -> str:
    raw = client.get_text(ref.url, headers={"User-Agent": sec_user_agent})
    return html_to_text(raw)


_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_TAG_RE = re.compile(r"</?(p|div|tr|br|li|h[1-6]|table)[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_NL_RE = re.compile(r"\n{2,}")


def html_to_text(html: str) -> str:
    """Plain text from filing HTML, preserving rough block structure."""
    text = _SCRIPT_RE.sub(" ", html)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n", text)
    return text.strip()

"""Shot-5 evidence helper: print substantive snippets from each sampled crash's
source document so the 2.5a doc-retrieval verdicts can be recorded honestly.
Read-only; uses the existing cache."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from stockbot.bots.proportionality.quality import fetch_event_filing_text
from stockbot.config import load_env
from stockbot.data.real_loader import SEC_SUBMISSIONS_URL, CachedHTTP

SAMPLE = [
    ("TLS", 320121, "0000320121-22-000123"),
    ("PGY", 1883085, "0001140361-22-033840"),
    ("RIGL", 1034842, "0001104659-22-069317"),
    ("ACRS", 1557746, "0001558370-23-018691"),
    ("DSGN", 1807120, "0000950170-23-042392"),
    ("ALTO", 778164, "0001213900-23-083888"),
    ("JBIO-2024", 1798749, "0001104659-24-072058"),
    ("AMLX", 1658551, "0001193125-24-062878"),
    ("SERV", 1832483, "0001213900-24-021137"),
    ("MLTX", 1821586, "0001213900-25-092614"),
    ("JBIO-2025", 1798749, "0001193125-25-103761"),
    ("AEBI", 2048519, "0001140361-25-024294"),
    ("VRRM", 1682745, "0001193125-26-239351"),
    ("REPL", 1737953, "0001104659-26-042141"),
]

KEYWORDS = re.compile(
    r"(announc|guidance|results|discontinu|topline|top-line|offering|terminat|"
    r"restructur|fail|did not meet|primary endpoint|withdraw|resign|halt|"
    r"reduc|lower|impair|default|delist|investigation)",
    re.I,
)


def main() -> int:
    env = load_env()
    client = CachedHTTP(cache_dir="fixtures/real_cache", offline=False)
    for label, cik, acc in SAMPLE:
        text = fetch_event_filing_text(
            client, cik=cik, accession_number=acc, sec_user_agent=env.sec_user_agent
        )
        flat = " ".join(text.split())
        hits = []
        for m in KEYWORDS.finditer(flat):
            start = max(0, m.start() - 120)
            hits.append(flat[start : m.end() + 240])
            if len(hits) >= 3:
                break
        print(f"\n===== {label} ({acc}) — {len(flat)} chars =====")
        for h in hits:
            print(f"  * …{h}…")
        if not hits:
            print("  (no keyword hits; head:)")
            print("  " + flat[:400])

    # CCXI (NONE row): list every filing within +/- 10 days of 2026-02-09.
    raw = client.get_json(
        SEC_SUBMISSIONS_URL.format(cik=1340652), headers={"User-Agent": env.sec_user_agent}
    )
    recent = raw.get("filings", {}).get("recent", {})
    print("\n===== CCXI filings near 2026-02-09 =====")
    for form, accn, fd in zip(
        recent.get("form", []), recent.get("accessionNumber", []), recent.get("filingDate", [])
    ):
        if "2026-01-30" <= fd <= "2026-02-19":
            print(f"  {fd}  {form}  {accn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

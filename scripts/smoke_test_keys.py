"""One-time smoke test: verify .env credentials work against all three data sources.

Prints status only — never prints key values. Safe to re-run anytime.
"""

from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    tiingo = os.environ.get("TIINGO_API_KEY", "")
    av = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    ua = os.environ.get("SEC_USER_AGENT", "")
    print(f"keys present -> tiingo: {bool(tiingo)} | alphavantage: {bool(av)} | sec_ua: {bool(ua)}")

    ok = True

    r = httpx.get(
        "https://api.tiingo.com/tiingo/daily/AAPL/prices",
        params={"startDate": "2026-06-01", "token": tiingo},
        timeout=30,
    )
    if r.status_code == 200:
        print(f"Tiingo: OK ({len(r.json())} rows)")
    else:
        ok = False
        print(f"Tiingo: FAIL {r.status_code} {r.text[:120]}")

    r2 = httpx.get(
        "https://www.alphavantage.co/query",
        params={"function": "EARNINGS", "symbol": "AAPL", "apikey": av},
        timeout=30,
    )
    rows = r2.json().get("quarterlyEarnings", []) if r2.status_code == 200 else []
    if rows:
        print(f"AlphaVantage: OK ({len(rows)} quarterly rows)")
    else:
        ok = False
        body = str(r2.json())[:150] if r2.status_code == 200 else r2.text[:150]
        print(f"AlphaVantage: FAIL {r2.status_code} {body}")

    r3 = httpx.get(
        "https://data.sec.gov/submissions/CIK0000320193.json",
        headers={"User-Agent": ua},
        timeout=30,
    )
    if r3.status_code == 200 and "filings" in r3.json():
        print("SEC EDGAR: OK (AAPL submissions readable)")
    else:
        ok = False
        print(f"SEC EDGAR: FAIL {r3.status_code} {r3.text[:120]}")

    print("RESULT:", "ALL GOOD" if ok else "FIX BEFORE SHOT 1")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

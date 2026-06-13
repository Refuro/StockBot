"""Shot-2 OPS: crumbs scanner tolerates per-form EDGAR FTS failures."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from stockbot.tools.crumbs.scanner import TENDER_FORMS, scan_odd_lot_tenders

_HIT = {
    "_source": {
        "adsh": "0001234567-26-000001",
        "ciks": ["0001234567"],
        "display_names": ["Acme Capital Corp"],
        "file_type": "SC TO-I",
        "file_date": "2026-06-01",
    },
    "highlight": {"text": ["odd lot holders will have priority"]},
}


class _FlakyFTSClient:
    """Fake CachedHTTP: some forms 500, others return one hit."""

    def __init__(self, failing_forms: set[str]) -> None:
        self.failing_forms = failing_forms
        self.queried: list[str] = []

    def get_json(self, url: str, params: dict, headers: dict) -> dict:
        form = params["forms"]
        self.queried.append(form)
        if form in self.failing_forms:
            raise httpx.HTTPStatusError(
                "500", request=httpx.Request("GET", url), response=httpx.Response(500)
            )
        return {"hits": {"hits": [_HIT]}}


def test_one_failing_form_does_not_kill_the_scan() -> None:
    client = _FlakyFTSClient(failing_forms={"SC 13E4", "SC TO-T"})
    alerts = scan_odd_lot_tenders(client, "test test@example.com", today=date(2026, 6, 11))
    assert client.queried == list(TENDER_FORMS)  # every form still attempted
    assert len(alerts) == 1
    assert alerts[0].company == "Acme Capital Corp"


def test_all_forms_failing_raises() -> None:
    client = _FlakyFTSClient(failing_forms=set(TENDER_FORMS))
    with pytest.raises(RuntimeError, match="all EDGAR FTS queries failed"):
        scan_odd_lot_tenders(client, "test test@example.com", today=date(2026, 6, 11))

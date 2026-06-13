"""Route-level smoke tests for the v3 dashboard.

These exercise the actual ``do_GET`` dispatch over a real HTTP server — the gap
that let the home ``/`` route 500 while 462 unit tests passed (the presentation
tests call render functions directly and never hit the route handlers). Every
route must return 200 on an EMPTY DB (the empty-state path is where None-handling
and arg-passing bugs hide).
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import HTTPServer

import pytest

from stockbot.core.state.store import StateStore
from stockbot.dashboard.app import DashboardPaths, make_handler

ROUTES = [
    "/",
    "/strategy/earnings_ride",
    "/strategy/crash_fade",
    "/strategy/frontier_up",
    "/strategy/frontier_down",
    "/strategy/bad_id",
    "/execution",
    "/ops",
    "/lens",
    "/glossary",
    "/debates",
    "/thesis/nonexistent",
]


@pytest.fixture()
def base_url(tmp_path):
    db = tmp_path / "paper.db"
    StateStore(str(db))  # creates the schema; empty DB
    paths = DashboardPaths(
        db_path=str(db),
        ops_db_path=str(db),
        logs_dir=str(tmp_path / "logs"),
        sidecar_path=str(tmp_path / "sidecar.json"),
        crumbs_json=str(tmp_path / "crumbs.json"),
    )
    server = HTTPServer(("127.0.0.1", 0), make_handler(paths))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _get(base_url: str, route: str) -> tuple[int, str]:
    # A crash in do_GET closes the connection without a response, which raises
    # here — that's the failure we want to catch (the home-route bug did exactly
    # this). No try/except: let it surface as a test failure.
    with urllib.request.urlopen(base_url + route, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


@pytest.mark.parametrize("route", ROUTES)
def test_route_returns_200_on_empty_db(base_url, route):
    status, body = _get(base_url, route)
    assert status == 200, f"{route} did not return 200"
    assert "<h1" in body or "<h2" in body, f"{route} did not render a real page"


def test_home_renders_scoreboard(base_url):
    # The shipped bug: '/' 500'd on a duplicate assumed_slippage_pct argument.
    status, body = _get(base_url, "/")
    assert status == 200
    assert "/strategy/earnings_ride" in body, "scoreboard cards missing"
    assert "Proportionality" in body and "Frontier" in body, "engine sections missing"


def test_unknown_strategy_is_friendly(base_url):
    status, body = _get(base_url, "/strategy/bad_id")
    assert status == 200
    assert "nknown" in body, "unknown strategy id should render a friendly page, not 500"

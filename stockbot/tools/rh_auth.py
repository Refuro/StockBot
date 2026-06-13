"""Phase 5a — one-time interactive Robinhood OAuth login.

Usage:
    python -m stockbot.tools.rh_auth login    # opens browser, captures callback,
                                              # stores secrets/robinhood_token.json
    python -m stockbot.tools.rh_auth status   # token presence/expiry, no network
    python -m stockbot.tools.rh_auth refresh  # force a refresh-token grant now

The flow (all parameters verified against the live discovery documents —
see core/broker/rh_oauth.py and docs/robinhood-mcp-reference.md):
1. Discover the authorization-server metadata for the MCP resource.
2. Dynamically register OUR OWN public client (one time; client_id is persisted
   alongside the tokens and reused).
3. Authorization-code + PKCE (S256) via the system browser, with the request
   resource-bound (RFC 8707) to the MCP URL; a localhost HTTP server captures
   the redirect.
4. Exchange the code at the token endpoint; store the token set with
   restricted permissions; print lifetime + refresh semantics so they can be
   documented in the reference doc.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets as pysecrets
import threading
import time
import urllib.parse
import webbrowser

import httpx

from stockbot.core.broker.rh_oauth import (
    DEFAULT_MCP_URL,
    DEFAULT_REDIRECT_URI,
    DEFAULT_TOKEN_PATH,
    SCOPE,
    OAuthServerMetadata,
    RobinhoodTokenStore,
    register_client,
)

CALLBACK_TIMEOUT_SECONDS = 1800  # 30 min — no timing pressure during interactive consent


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures ?code=...&state=... from the OAuth redirect."""

    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        query = urllib.parse.urlparse(self.path).query
        params = dict(urllib.parse.parse_qsl(query))
        type(self).captured = params
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        body = (
            "<html><body><h2>Stockbot: authorization received.</h2>"
            "<p>You can close this tab and return to the terminal.</p></body></html>"
            if "code" in params
            else "<html><body><h2>Stockbot: authorization FAILED.</h2>"
            f"<p>{params.get('error', 'no code in callback')}</p></body></html>"
        )
        self.wfile.write(body.encode())

    def log_message(self, *args) -> None:  # silence default request logging
        pass


def login(mcp_url: str = DEFAULT_MCP_URL, token_path=DEFAULT_TOKEN_PATH) -> int:
    http_client = httpx.Client(timeout=30.0)
    meta = OAuthServerMetadata.discover(mcp_url, client=http_client)
    print(f"authorization endpoint: {meta.authorization_endpoint}")
    print(f"token endpoint:         {meta.token_endpoint}")

    store = RobinhoodTokenStore(token_path, http_client=http_client, mcp_url=mcp_url)
    client_id: str | None = None
    if store.path.exists():
        try:
            client_id = store.load().get("client_id")
        except Exception:  # noqa: BLE001 - corrupt file: re-register
            client_id = None
    if not client_id:
        client_id = register_client(meta, client=http_client)
        print(f"registered new public client: {client_id[:8]}…")

    verifier, challenge = _pkce_pair()
    state = pysecrets.token_urlsafe(24)
    redirect = urllib.parse.urlparse(DEFAULT_REDIRECT_URI)
    auth_url = meta.authorization_endpoint + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": mcp_url,  # RFC 8707 resource binding
        }
    )

    _CallbackHandler.captured = {}
    server = http.server.HTTPServer((redirect.hostname, redirect.port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"\nOpening browser for Robinhood consent…\nIf it does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    deadline = time.time() + CALLBACK_TIMEOUT_SECONDS
    try:
        while time.time() < deadline and "code" not in _CallbackHandler.captured \
                and "error" not in _CallbackHandler.captured:
            time.sleep(0.25)
    finally:
        server.shutdown()
    captured = _CallbackHandler.captured
    if "code" not in captured:
        print(f"login failed or timed out: {captured.get('error', 'no callback received')}")
        return 1
    if captured.get("state") != state:
        print("login failed: state mismatch (possible CSRF) — aborting")
        return 1

    resp = http_client.post(
        meta.token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": captured["code"],
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": mcp_url,
        },
        headers={"Accept": "application/json"},
    )
    if resp.status_code >= 400:
        print(f"token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
        return 1
    token = resp.json()
    data = store.store_token_response(token, client_id=client_id, token_endpoint=meta.token_endpoint)

    # Document lifetime/refresh semantics (the 5a open question) — values only,
    # never the tokens themselves.
    print("\n=== TOKEN SEMANTICS (document in docs/robinhood-mcp-reference.md) ===")
    print(f"  expires_in:        {token.get('expires_in')} seconds")
    print(f"  refresh_token:     {'present' if data.get('refresh_token') else 'ABSENT'}")
    print(f"  token_type:        {token.get('token_type')}")
    print(f"  scope:             {token.get('scope')}")
    print(f"  extra keys:        {sorted(set(token) - {'access_token', 'refresh_token', 'expires_in', 'token_type', 'scope'})}")
    print(f"\ntoken stored at {store.path} (restricted permissions)")
    return 0


def status(token_path=DEFAULT_TOKEN_PATH) -> int:
    store = RobinhoodTokenStore(token_path)
    if not store.path.exists():
        print(f"no token at {store.path} — run `python -m stockbot.tools.rh_auth login`")
        return 1
    data = store.load()
    expires_at = data.get("expires_at")
    remaining = (expires_at - time.time()) if expires_at else None
    print(json.dumps(
        {
            "client_id_prefix": str(data.get("client_id", ""))[:8] + "…",
            "has_refresh_token": bool(data.get("refresh_token")),
            "expires_in_seconds": round(remaining) if remaining is not None else None,
            "expired": (remaining is not None and remaining <= 0),
            "scope": data.get("scope"),
        },
        indent=2,
    ))
    return 0


def refresh(token_path=DEFAULT_TOKEN_PATH) -> int:
    store = RobinhoodTokenStore(token_path)
    data = store.refresh()
    print(f"refreshed; new expiry in {data.get('expires_in')} seconds")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Robinhood MCP OAuth login (Phase 5a)")
    parser.add_argument("command", choices=["login", "status", "refresh"])
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--token-path", default=str(DEFAULT_TOKEN_PATH))
    args = parser.parse_args(argv)
    if args.command == "login":
        return login(args.mcp_url, args.token_path)
    if args.command == "status":
        return status(args.token_path)
    return refresh(args.token_path)


if __name__ == "__main__":
    raise SystemExit(main())

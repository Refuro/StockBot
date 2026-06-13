"""Manual paste-based Robinhood OAuth login — a fallback for when the localhost
callback capture is flaky (browser doesn't reach the listener, process timing).

Two phases, decoupled from any live server:

    python scripts/rh_login_manual.py url
        -> discovers the auth server, registers our public client, prints the
           consent URL, and SAVES the PKCE verifier + state to
           secrets/.rh_login_pending.json

    python scripts/rh_login_manual.py exchange "<callback-url-or-code>"
        -> reads the pending verifier, exchanges the authorization code at the
           token endpoint, and stores the token via the normal token store.

The verifier never leaves this machine; the pending file is deleted on success.
Same token store / format as `python -m stockbot.tools.rh_auth login`, so
`rh_auth status` / the broker read it identically.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets as pysecrets
import sys
import urllib.parse
from pathlib import Path

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

PENDING = Path("secrets/.rh_login_pending.json")


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def cmd_url() -> int:
    client = httpx.Client(timeout=30.0)
    meta = OAuthServerMetadata.discover(DEFAULT_MCP_URL, client=client)
    store = RobinhoodTokenStore(DEFAULT_TOKEN_PATH, http_client=client, mcp_url=DEFAULT_MCP_URL)

    client_id: str | None = None
    if store.path.exists():
        try:
            client_id = store.load().get("client_id")
        except Exception:  # noqa: BLE001 - corrupt file: re-register
            client_id = None
    if not client_id:
        client_id = register_client(meta, client=client)

    verifier, challenge = _pkce_pair()
    state = pysecrets.token_urlsafe(24)
    auth_url = meta.authorization_endpoint + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": DEFAULT_MCP_URL,
        }
    )
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(
        json.dumps(
            {
                "verifier": verifier,
                "state": state,
                "client_id": client_id,
                "token_endpoint": meta.token_endpoint,
            }
        ),
        encoding="utf-8",
    )
    print("CONSENT URL (open, log in, click Allow):\n")
    print(auth_url)
    print(
        "\nAfter you click Allow, the browser will try to reach "
        "http://localhost:8742/callback —\n"
        "it does NOT matter if that page shows an error. Copy the FULL address-bar\n"
        "URL (it contains ?code=...&state=...) and run:\n\n"
        '  python scripts/rh_login_manual.py exchange "<that-url>"\n'
    )
    return 0


def cmd_exchange(arg: str) -> int:
    if not PENDING.exists():
        print("no pending login — run `python scripts/rh_login_manual.py url` first")
        return 1
    pend = json.loads(PENDING.read_text(encoding="utf-8"))

    code = arg.strip()
    if "code=" in code:
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(code).query))
        if params.get("state") and params["state"] != pend["state"]:
            print(
                "WARNING: state in the pasted URL does not match the pending login.\n"
                "         Use the URL from the most recent `... url` run, or just\n"
                "         paste the bare code value."
            )
        code = params.get("code", code)

    client = httpx.Client(timeout=30.0)
    resp = client.post(
        pend["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "client_id": pend["client_id"],
            "code_verifier": pend["verifier"],
            "resource": DEFAULT_MCP_URL,
        },
        headers={"Accept": "application/json"},
    )
    if resp.status_code >= 400:
        print(f"token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
        return 1

    token = resp.json()
    store = RobinhoodTokenStore(DEFAULT_TOKEN_PATH, http_client=client, mcp_url=DEFAULT_MCP_URL)
    data = store.store_token_response(
        token, client_id=pend["client_id"], token_endpoint=pend["token_endpoint"]
    )
    PENDING.unlink(missing_ok=True)

    print("=== TOKEN STORED ===")
    print(f"  path:          {store.path}")
    print(f"  expires_in:    {token.get('expires_in')} seconds")
    print(f"  refresh_token: {'present' if data.get('refresh_token') else 'ABSENT'}")
    print(f"  token_type:    {token.get('token_type')}")
    print(f"  scope:         {token.get('scope')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in {"url", "exchange"}:
        print("usage: rh_login_manual.py url | exchange \"<callback-url-or-code>\"")
        return 2
    if args[0] == "url":
        return cmd_url()
    if len(args) < 2:
        print('usage: rh_login_manual.py exchange "<callback-url-or-code>"')
        return 2
    return cmd_exchange(args[1])


if __name__ == "__main__":
    raise SystemExit(main())

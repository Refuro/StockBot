"""Phase 5a — Robinhood OAuth 2.0 + PKCE token management.

Discovery chain (verified live 2026-06-12, see docs/robinhood-mcp-reference.md):

1. ``GET https://agent.robinhood.com/mcp/trading`` unauthenticated → 401 with
   ``WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/mcp/trading"``
2. Protected-resource metadata → ``authorization_servers: [<the MCP URL>]``
3. ``GET /.well-known/oauth-authorization-server/mcp/trading`` →
   - authorization_endpoint: https://robinhood.com/oauth
   - token_endpoint:         https://api.robinhood.com/oauth2/token/
   - registration_endpoint:  https://agent.robinhood.com/oauth/trading/register  (DCR is OPEN)
   - code_challenge_methods: [S256]; grant_types: [authorization_code, refresh_token]
   - token_endpoint_auth_methods: [none]  → public client + PKCE, no client secret

Dynamic client registration works (verified live: POST with redirect_uris →
200 + client_id), so we register our OWN client_id — never reuse another
client's. Tokens live in ``secrets/robinhood_token.json`` (gitignored,
permissions restricted best-effort on Windows), are auto-refreshed on expiry,
and a dead refresh raises ``RobinhoodAuthError`` telling the user to re-run
``python -m stockbot.tools.rh_auth login``.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://agent.robinhood.com/mcp/trading"
DEFAULT_TOKEN_PATH = Path("secrets") / "robinhood_token.json"
DEFAULT_REDIRECT_URI = "http://localhost:8742/callback"
CLIENT_NAME = "Stockbot RobinhoodMCPBroker"
SCOPE = "internal"

# Refresh this many seconds before nominal expiry so a token never dies mid-call.
EXPIRY_SLACK_SECONDS = 120


class RobinhoodAuthError(RuntimeError):
    """Auth is unrecoverable without user interaction (re-run rh_auth login)."""


@dataclass
class OAuthServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    issuer: str

    @classmethod
    def discover(cls, mcp_url: str = DEFAULT_MCP_URL, *, client: httpx.Client | None = None) -> "OAuthServerMetadata":
        """RFC 8414 path-aware metadata for the MCP resource's authorization server."""
        http = client or httpx.Client(timeout=20.0)
        from urllib.parse import urlparse

        parsed = urlparse(mcp_url)
        meta_url = f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server{parsed.path}"
        resp = http.get(meta_url)
        resp.raise_for_status()
        data = resp.json()
        return cls(
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            registration_endpoint=data.get("registration_endpoint"),
            issuer=data.get("issuer", mcp_url),
        )


def register_client(
    metadata: OAuthServerMetadata,
    *,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    client: httpx.Client | None = None,
) -> str:
    """MCP dynamic client registration (RFC 7591). Returns the client_id."""
    if not metadata.registration_endpoint:
        raise RobinhoodAuthError(
            "authorization server advertises no registration_endpoint — dynamic "
            "client registration is closed; STOP and flag the reviewer (never "
            "scrape or spoof a client id)"
        )
    http = client or httpx.Client(timeout=20.0)
    resp = http.post(
        metadata.registration_endpoint,
        json={
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        },
    )
    if resp.status_code >= 400:
        raise RobinhoodAuthError(
            f"dynamic client registration failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    return str(resp.json()["client_id"])


def _restrict_permissions(path: Path) -> None:
    """Owner read/write only — best-effort on Windows (NTFS ignores POSIX bits,
    but the file also lives under the user profile and secrets/ is gitignored)."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - platform-dependent
        logger.warning("could not restrict permissions on %s", path)


class RobinhoodTokenStore:
    """Loads/saves/refreshes the OAuth token set in secrets/robinhood_token.json."""

    def __init__(
        self,
        token_path: str | Path = DEFAULT_TOKEN_PATH,
        *,
        http_client: httpx.Client | None = None,
        mcp_url: str = DEFAULT_MCP_URL,
    ) -> None:
        self._path = Path(token_path)
        self._http = http_client or httpx.Client(timeout=20.0)
        self._mcp_url = mcp_url
        self._data: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        if self._data is None:
            if not self._path.exists():
                raise RobinhoodAuthError(
                    f"no Robinhood token at {self._path} — run "
                    "`python -m stockbot.tools.rh_auth login` once to authorize"
                )
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        return self._data

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _restrict_permissions(self._path)
        self._data = data

    def store_token_response(
        self,
        token_response: dict[str, Any],
        *,
        client_id: str,
        token_endpoint: str,
    ) -> dict[str, Any]:
        """Persist a token-endpoint response (login or refresh), computing
        absolute expiry from ``expires_in``."""
        now = time.time()
        prior = self._data or {}
        data = {
            "client_id": client_id,
            "token_endpoint": token_endpoint,
            "mcp_url": self._mcp_url,
            "access_token": token_response["access_token"],
            # Some servers omit the refresh token on refresh — keep the old one.
            "refresh_token": token_response.get("refresh_token") or prior.get("refresh_token"),
            "token_type": token_response.get("token_type", "Bearer"),
            "scope": token_response.get("scope", SCOPE),
            "expires_in": token_response.get("expires_in"),
            "obtained_at": now,
            "expires_at": (now + float(token_response["expires_in"]))
            if token_response.get("expires_in") else None,
        }
        self.save(data)
        return data

    def access_token(self) -> str:
        """Current access token, refreshed automatically if (nearly) expired."""
        data = self.load()
        expires_at = data.get("expires_at")
        if expires_at is not None and time.time() >= float(expires_at) - EXPIRY_SLACK_SECONDS:
            data = self.refresh()
        return str(data["access_token"])

    def refresh(self) -> dict[str, Any]:
        """refresh_token grant. Raises RobinhoodAuthError when re-auth is needed."""
        data = self.load()
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            raise RobinhoodAuthError(
                "token expired and no refresh_token stored — run "
                "`python -m stockbot.tools.rh_auth login` to re-authorize"
            )
        resp = self._http.post(
            data["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": data["client_id"],
                # RFC 8707: keep the token bound to the MCP resource.
                "resource": data.get("mcp_url", self._mcp_url),
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code >= 400:
            raise RobinhoodAuthError(
                f"token refresh failed (HTTP {resp.status_code}: {resp.text[:200]}) — "
                "run `python -m stockbot.tools.rh_auth login` to re-authorize"
            )
        logger.info("Robinhood access token refreshed")
        return self.store_token_response(
            resp.json(), client_id=data["client_id"], token_endpoint=data["token_endpoint"]
        )

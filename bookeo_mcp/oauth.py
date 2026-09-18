"""Single-user OAuth for remote MCP clients.

Claude's custom connectors (claude.ai, desktop, mobile) can only authenticate
with OAuth: they register themselves, send the user through /authorize, and
then use the resulting tokens. This module is the authorization server behind
the MCP SDK's OAuth routes.

There is one user, identified by knowing AUTH_TOKEN, which they type into the
login page once per connector. Nothing is stored: client ids, authorization
codes and tokens are all HMAC-signed payloads, so they survive restarts and
redeploys without a volume. Rotating AUTH_TOKEN invalidates every one of them.
The cost is that an individual token cannot be revoked before it expires.
"""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from typing import Optional

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

LOGIN_TTL = 10 * 60
CODE_TTL = 5 * 60
ACCESS_TOKEN_TTL = 60 * 60
# Each refresh issues a new refresh token, so a connector in regular use never
# has to log in again; one left idle this long does.
REFRESH_TOKEN_TTL = 90 * 24 * 60 * 60


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class Signer:
    """Signs and verifies typed, optionally expiring JSON payloads."""

    def __init__(self, secret: str):
        self._key = hmac.new(
            secret.encode(), b"bookeo-mcp-oauth-v1", hashlib.sha256
        ).digest()

    def _mac(self, data: bytes) -> bytes:
        return hmac.new(self._key, data, hashlib.sha256).digest()

    def derive(self, label: str, value: str) -> str:
        return _b64encode(self._mac(f"{label}:{value}".encode()))

    def sign(self, typ: str, payload: dict, ttl: Optional[int] = None) -> str:
        body = {**payload, "typ": typ}
        if ttl is not None:
            body["exp"] = int(time.time()) + ttl
        data = json.dumps(body, separators=(",", ":")).encode()
        return f"{_b64encode(data)}.{_b64encode(self._mac(data))}"

    def verify(self, typ: str, token: str) -> Optional[dict]:
        try:
            data_part, mac_part = token.split(".")
            data = _b64decode(data_part)
            if not hmac.compare_digest(_b64decode(mac_part), self._mac(data)):
                return None
            body = json.loads(data)
        except (ValueError, TypeError):
            return None
        # The type stops one kind of token being replayed as another
        if not isinstance(body, dict) or body.get("typ") != typ:
            return None
        if "exp" in body and body["exp"] < time.time():
            return None
        return body


class BookeoOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, auth_token: str, public_url: str):
        self._auth_token = auth_token
        self._public_url = public_url.rstrip("/")
        self._signer = Signer(auth_token)
        # Authorization codes are single use. This is the only state, and
        # losing it in a restart only matters for the few minutes a code lives.
        self._used_codes: dict[str, float] = {}
        self._login_lock = asyncio.Lock()

    def _client_ref(self, client_id: Optional[str]) -> str:
        """Short stand-in for a client id, which is itself a signed payload."""
        return hashlib.sha256((client_id or "").encode()).hexdigest()[:32]

    # Clients

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # The registration handler returns this same object to the client, so
        # replacing its id and secret here is what the client receives.
        client_info.client_id = self._signer.sign(
            "client",
            {
                "redirect_uris": [str(u) for u in client_info.redirect_uris or []],
                "auth_method": client_info.token_endpoint_auth_method,
                "name": client_info.client_name,
                "scope": client_info.scope,
                "n": secrets.token_urlsafe(8),
            },
        )
        if client_info.client_secret:
            client_info.client_secret = self._signer.derive(
                "client-secret", client_info.client_id
            )

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        body = self._signer.verify("client", client_id)
        if body is None:
            return None
        auth_method = body.get("auth_method") or "none"
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=(
                None
                if auth_method == "none"
                else self._signer.derive("client-secret", client_id)
            ),
            redirect_uris=body["redirect_uris"],
            token_endpoint_auth_method=auth_method,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            client_name=body.get("name"),
            scope=body.get("scope"),
        )

    # Authorization

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        login = self._signer.sign(
            "login",
            {
                "cid": self._client_ref(client.client_id),
                "client_name": client.client_name,
                "state": params.state,
                "scopes": params.scopes or [],
                "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "explicit": params.redirect_uri_provided_explicitly,
                "resource": params.resource,
            },
            ttl=LOGIN_TTL,
        )
        return construct_redirect_uri(f"{self._public_url}/login", tx=login)

    async def handle_login(self, request: Request) -> Response:
        """The page /authorize redirects to: ask for AUTH_TOKEN, issue a code."""
        if request.method == "GET":
            tx, password = request.query_params.get("tx", ""), None
        else:
            form = await request.form()
            # Pasted tokens pick up stray whitespace; a token never contains any
            tx, password = str(form.get("tx", "")), str(form.get("password", "")).strip()

        login = self._signer.verify("login", tx)
        if login is None:
            return HTMLResponse(
                _page("This sign-in link is invalid or has expired. "
                      "Start again from Claude."),
                status_code=400,
            )
        if password is None:
            return HTMLResponse(_login_form(tx, login.get("client_name")))

        # One attempt at a time, and a failed one is slow, so the token can't
        # be guessed at any useful rate.
        async with self._login_lock:
            if not hmac.compare_digest(password.encode(), self._auth_token.encode()):
                await asyncio.sleep(1)
                return HTMLResponse(
                    _login_form(tx, login.get("client_name"), failed=True),
                    status_code=401,
                )

        code = self._signer.sign(
            "code",
            {
                k: login[k]
                for k in (
                    "cid", "scopes", "code_challenge", "redirect_uri",
                    "explicit", "resource",
                )
            }
            | {"jti": secrets.token_urlsafe(16)},
            ttl=CODE_TTL,
        )
        return RedirectResponse(
            construct_redirect_uri(
                login["redirect_uri"], code=code, state=login.get("state")
            ),
            status_code=302,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        body = self._signer.verify("code", authorization_code)
        if body is None or body["cid"] != self._client_ref(client.client_id):
            return None
        if body["jti"] in self._used_codes:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=body["scopes"],
            expires_at=body["exp"],
            client_id=client.client_id or "",
            code_challenge=body["code_challenge"],
            redirect_uri=body["redirect_uri"],
            redirect_uri_provided_explicitly=body["explicit"],
            resource=body.get("resource"),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        now = time.time()
        self._used_codes = {j: e for j, e in self._used_codes.items() if e > now}
        body = self._signer.verify("code", authorization_code.code) or {}
        if "jti" in body:
            self._used_codes[body["jti"]] = body["exp"]
        return self._issue_tokens(
            client, authorization_code.scopes, authorization_code.resource
        )

    # Tokens

    def _issue_tokens(
        self, client: OAuthClientInformationFull, scopes: list[str], resource: Optional[str]
    ) -> OAuthToken:
        claims = {
            "cid": self._client_ref(client.client_id),
            "scopes": scopes,
            "resource": resource,
        }
        return OAuthToken(
            access_token=self._signer.sign(
                "access", claims | {"n": secrets.token_urlsafe(8)}, ttl=ACCESS_TOKEN_TTL
            ),
            refresh_token=self._signer.sign(
                "refresh", claims | {"n": secrets.token_urlsafe(8)}, ttl=REFRESH_TOKEN_TTL
            ),
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(scopes) if scopes else None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        body = self._signer.verify("refresh", refresh_token)
        if body is None or body["cid"] != self._client_ref(client.client_id):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id or "",
            scopes=body["scopes"],
            expires_at=body["exp"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        body = self._signer.verify("refresh", refresh_token.token) or {}
        return self._issue_tokens(
            client, scopes or refresh_token.scopes, body.get("resource")
        )

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        # Clients that can send a header, like Claude Code, use AUTH_TOKEN itself
        if hmac.compare_digest(token.encode(), self._auth_token.encode()):
            return AccessToken(token=token, client_id="static-token", scopes=[])
        body = self._signer.verify("access", token)
        if body is None:
            return None
        return AccessToken(
            token=token,
            client_id=body["cid"],
            scopes=body["scopes"],
            expires_at=body["exp"],
            resource=body.get("resource"),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Nothing is stored, so there is nothing to revoke; rotate AUTH_TOKEN.
        return None


def _page(body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Bookeo MCP sign-in</title>
<style>
  body {{ font: 16px/1.5 system-ui, sans-serif; background: #f4f4f2; color: #222;
         display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  main {{ background: #fff; padding: 2rem; border-radius: 12px; width: min(22rem, 90vw);
         box-shadow: 0 2px 12px rgba(0,0,0,.08); }}
  h1 {{ font-size: 1.15rem; margin: 0 0 .5rem; }}
  p {{ margin: 0 0 1rem; color: #555; }}
  input, button {{ font: inherit; width: 100%; box-sizing: border-box; padding: .6rem .7rem;
                   border-radius: 8px; border: 1px solid #bbb; }}
  button {{ margin-top: .75rem; background: #222; color: #fff; border: 0; cursor: pointer; }}
  .error {{ color: #b3261e; }}
</style></head><body><main><h1>Bookeo MCP</h1>{body}</main></body></html>"""


def _login_form(tx: str, client_name: Optional[str], failed: bool = False) -> str:
    who = html.escape(client_name or "An application")
    error = '<p class="error">That token is not correct.</p>' if failed else ""
    return _page(
        f"""<p>{who} is asking for access to Escape Key's bookings.
Enter the server's access token to allow it.</p>{error}
<form method="post" action="/login">
  <input type="hidden" name="tx" value="{html.escape(tx, quote=True)}">
  <input type="password" name="password" placeholder="Access token"
         autocomplete="off" data-1p-ignore data-lpignore="true" autofocus required>
  <button type="submit">Allow access</button>
</form>"""
    )

"""Installed-app OAuth flow for Gmail (loopback redirect, PKCE, readonly scope)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from rollup.gmail.session import (
    AUTH_ENDPOINT,
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    READONLY_SCOPE,
    GmailAuthError,
    client_credentials,
    exchange_token,
    write_token_file,
)

LOOPBACK_HOST = "127.0.0.1"
AUTH_WAIT_SECONDS = 300


@dataclass(frozen=True)
class PendingAuth:
    client_id: str
    client_secret: str
    redirect_uri: str
    state: str
    code_verifier: str
    auth_url: str


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_pending_auth(*, redirect_uri: str) -> PendingAuth:
    creds = client_credentials()
    if creds is None:
        raise GmailAuthError(
            f"Set {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} in ~/.config/rollup/env "
            "(Google Cloud → OAuth client, type 'Desktop app') before running auth"
        )
    client_id, client_secret = creds
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return PendingAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        state=state,
        code_verifier=verifier,
        auth_url=f"{AUTH_ENDPOINT}?{urlencode(params)}",
    )


def parse_redirect(pending: PendingAuth, redirect_url_or_query: str) -> str:
    """Extract and validate the authorization code from a redirect URL/query."""
    text = redirect_url_or_query.strip()
    query = urlparse(text).query if "?" in text else text.lstrip("?")
    params = parse_qs(query)
    if params.get("error"):
        raise GmailAuthError(f"Google returned error: {params['error'][0]}")
    state = (params.get("state") or [""])[0]
    if state != pending.state:
        raise GmailAuthError("OAuth state mismatch; start `rollup gmail auth` again")
    code = (params.get("code") or [""])[0]
    if not code:
        raise GmailAuthError("Redirect did not contain an authorization code")
    return code


def finish_auth(
    pending: PendingAuth,
    code: str,
    *,
    token_path: Path,
    session: requests.Session | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Exchange the code for tokens and persist the token file (mode 600)."""
    http = session or requests.Session()
    payload = exchange_token(
        http,
        client_id=pending.client_id,
        client_secret=pending.client_secret,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.code_verifier,
        },
        label="Gmail token exchange",
    )
    refresh = payload.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise GmailAuthError(
            "Google did not return a refresh token; revoke Rollup at "
            "myaccount.google.com/permissions and run auth again"
        )
    scope = str(payload.get("scope") or READONLY_SCOPE)
    if READONLY_SCOPE not in scope.split():
        raise GmailAuthError(f"Granted scope is not gmail.readonly: {scope}")
    current = now if now is not None else time.time()
    try:
        expires_at = current + float(payload.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_at = current + 3600.0
    data = {
        "refresh_token": refresh,
        "access_token": str(payload["access_token"]),
        "expires_at": expires_at,
        "scope": scope,
        "token_type": str(payload.get("token_type") or "Bearer"),
        "obtained_at": current,
    }
    write_token_file(token_path, data)
    return data


class _RedirectHandler(BaseHTTPRequestHandler):
    """Capture exactly one loopback redirect; everything else gets 404."""

    captured: dict[str, str] = {}
    done = threading.Event()

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        if parsed.path not in ("/", ""):
            self.send_response(404)
            self.end_headers()
            return
        type(self).captured["query"] = parsed.query
        body = (
            b"<html><body><h2>Rollup: Gmail authorised.</h2>"
            b"<p>You can close this tab and return to the terminal.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).done.set()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def run_loopback_auth(
    *,
    token_path: Path,
    open_browser: bool = True,
    manual: bool = False,
    emit=print,
    prompt=input,
    wait_seconds: int = AUTH_WAIT_SECONDS,
) -> dict[str, Any]:
    """Interactive flow used by ``rollup gmail auth``.

    ``manual`` skips the loopback listener: the user pastes the redirect URL
    (useful inside containers or over SSH where the browser cannot reach back).
    """
    if manual:
        pending = build_pending_auth(redirect_uri=f"http://{LOOPBACK_HOST}:1")
        emit("Open this URL in a browser, sign in, then paste the final redirect URL:")
        emit(pending.auth_url)
        pasted = prompt("Redirect URL: ")
        code = parse_redirect(pending, pasted)
        return finish_auth(pending, code, token_path=token_path)

    handler = type("RollupRedirectHandler", (_RedirectHandler,), {})
    handler.captured = {}
    handler.done = threading.Event()
    server = HTTPServer((LOOPBACK_HOST, 0), handler)
    port = server.server_address[1]
    redirect_uri = f"http://{LOOPBACK_HOST}:{port}"
    pending = build_pending_auth(redirect_uri=redirect_uri)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        emit("Authorise Rollup (read-only Gmail) in your browser:")
        emit(pending.auth_url)
        if open_browser:
            try:
                webbrowser.open(pending.auth_url)
            except Exception:  # pragma: no cover - browser launch is best-effort
                pass
        if not handler.done.wait(timeout=wait_seconds):
            raise GmailAuthError("Timed out waiting for the browser redirect")
    finally:
        server.shutdown()
        server.server_close()
    code = parse_redirect(pending, handler.captured.get("query", ""))
    return finish_auth(pending, code, token_path=token_path)

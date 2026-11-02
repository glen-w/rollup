"""Gmail REST client over ``requests`` with OAuth refresh (read-only scope).

Client credentials come from the environment (``~/.config/rollup/env``);
the refresh token lives in ``{state_dir}/gmail_token.json`` (mode 600).
Nothing here ever writes to Gmail: the only scope requested is
``gmail.readonly`` and only ``GET`` endpoints are called.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from rollup import __version__

CLIENT_ID_ENV = "ROLLUP_GMAIL_CLIENT_ID"
CLIENT_SECRET_ENV = "ROLLUP_GMAIL_CLIENT_SECRET"
TOKEN_FILE_NAME = "gmail_token.json"

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

REQUEST_TIMEOUT = 30
RATE_LIMIT_MAX_RETRIES = 4
RATE_LIMIT_BASE_WAIT = 2.0
# Refresh the access token when fewer than this many seconds remain.
TOKEN_REFRESH_MARGIN_SECONDS = 120


class GmailSessionError(RuntimeError):
    """Gmail fetch failed (network, HTTP, or payload shape)."""

    def __init__(self, message: str, *, retriable: bool = True) -> None:
        super().__init__(message)
        self.retriable = retriable


class GmailAuthError(GmailSessionError):
    """Credentials missing, token file absent, or refresh rejected."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=False)


class GmailNotFoundError(GmailSessionError):
    """Label or message not found (do not retry)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=False)


@dataclass(frozen=True)
class GmailRawMessage:
    gmail_id: str
    thread_id: str
    label_ids: tuple[str, ...]
    internal_date: datetime | None
    raw: bytes
    size_estimate: int


def gmail_user_agent() -> str:
    return f"rollup/{__version__}"


def client_credentials() -> tuple[str, str] | None:
    """Installed-app client id/secret from env; None when unset."""
    client_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    client_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def credentials_configured() -> bool:
    return client_credentials() is not None


def token_path_for(state_dir: Path) -> Path:
    return state_dir / TOKEN_FILE_NAME


def token_configured(state_dir: Path) -> bool:
    return token_path_for(state_dir).is_file()


def read_token_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GmailAuthError(
            f"Gmail token file missing: {path} (run `rollup gmail auth`)"
        ) from exc
    except (OSError, ValueError) as exc:
        raise GmailAuthError(f"Gmail token file unreadable: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("refresh_token"), str):
        raise GmailAuthError(
            f"Gmail token file has no refresh_token: {path} (run `rollup gmail auth`)"
        )
    return data


def write_token_file(path: Path, data: dict[str, Any]) -> None:
    """Write token JSON atomically with mode 600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def token_scope_is_readonly(data: dict[str, Any]) -> bool:
    scope = str(data.get("scope", "") or "")
    scopes = {s for s in scope.split() if s}
    return scopes == {READONLY_SCOPE} if scopes else True


def format_http_error(resp: requests.Response, *, label: str) -> str:
    content_type = resp.headers.get("Content-Type", "")
    body = (resp.text or "").strip().replace("\n", " ")[:200]
    parts = [f"{label} failed ({resp.status_code})", f"content-type={content_type}"]
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        parts.append(f"retry-after={retry_after}")
    if body:
        parts.append(body)
    return ": ".join(parts)


def exchange_token(
    session: requests.Session,
    *,
    client_id: str,
    client_secret: str,
    form: dict[str, str],
    label: str,
) -> dict[str, Any]:
    """POST to the Google token endpoint and return the JSON payload."""
    data = dict(form)
    data["client_id"] = client_id
    data["client_secret"] = client_secret
    resp = session.post(
        TOKEN_ENDPOINT,
        data=data,
        headers={"User-Agent": gmail_user_agent()},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise GmailAuthError(format_http_error(resp, label=label))
    try:
        payload = resp.json()
    except ValueError as exc:
        raise GmailAuthError(f"{label}: token response is not JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        raise GmailAuthError(f"{label}: token response missing access_token")
    return payload


def _parse_internal_date(raw: object) -> datetime | None:
    try:
        millis = int(str(raw))
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)


def decode_raw(raw_b64url: str) -> bytes:
    text = raw_b64url.strip()
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class GmailClient:
    """Thin read-only Gmail REST client with token refresh and 429/5xx backoff."""

    def __init__(
        self,
        token_path: Path,
        *,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self._token_path = token_path
        self._session = session
        self._sleep = sleep
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self.requests_made = 0

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    # -- auth -------------------------------------------------------------

    def _ensure_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            return self._access_token
        creds = client_credentials()
        if creds is None:
            raise GmailAuthError(
                f"Gmail client credentials missing: set {CLIENT_ID_ENV} and "
                f"{CLIENT_SECRET_ENV} in ~/.config/rollup/env"
            )
        data = read_token_file(self._token_path)
        if not token_scope_is_readonly(data):
            raise GmailAuthError(
                "Gmail token was granted a scope other than gmail.readonly; "
                "re-run `rollup gmail auth`"
            )
        cached_token = data.get("access_token")
        cached_exp = data.get("expires_at")
        if (
            isinstance(cached_token, str)
            and isinstance(cached_exp, (int, float))
            and now < float(cached_exp) - TOKEN_REFRESH_MARGIN_SECONDS
        ):
            self._access_token = cached_token
            self._expires_at = float(cached_exp)
            return cached_token
        client_id, client_secret = creds
        payload = exchange_token(
            self._http(),
            client_id=client_id,
            client_secret=client_secret,
            form={
                "grant_type": "refresh_token",
                "refresh_token": str(data["refresh_token"]),
            },
            label="Gmail token refresh",
        )
        expires_in = payload.get("expires_in", 3600)
        try:
            expires_at = now + float(expires_in)
        except (TypeError, ValueError):
            expires_at = now + 3600.0
        self._access_token = str(payload["access_token"])
        self._expires_at = expires_at
        data["access_token"] = self._access_token
        data["expires_at"] = expires_at
        if isinstance(payload.get("scope"), str):
            data["scope"] = payload["scope"]
        try:
            write_token_file(self._token_path, data)
        except OSError:
            pass
        return self._access_token

    # -- transport --------------------------------------------------------

    def _get(self, path: str, *, params: dict[str, Any] | None = None, label: str) -> Any:
        url = f"{GMAIL_API_BASE}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)}"
        session = self._http()
        attempt = 0
        while True:
            token = self._ensure_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": gmail_user_agent(),
                "Accept": "application/json",
            }
            self.requests_made += 1
            try:
                resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                raise GmailSessionError(f"{label} failed: {exc}") from exc
            if resp.status_code == 401:
                # Access token rejected: force a refresh once, then give up.
                if attempt == 0:
                    self._access_token = None
                    self._expires_at = 0.0
                    attempt += 1
                    continue
                raise GmailAuthError(format_http_error(resp, label=label))
            if resp.status_code == 404:
                raise GmailNotFoundError(format_http_error(resp, label=label))
            if resp.status_code in (429, 500, 502, 503) and attempt < RATE_LIMIT_MAX_RETRIES:
                wait = RATE_LIMIT_BASE_WAIT * (2**attempt)
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except (TypeError, ValueError):
                        pass
                self._sleep(wait)
                attempt += 1
                continue
            if resp.status_code != 200:
                raise GmailSessionError(format_http_error(resp, label=label))
            try:
                return resp.json()
            except ValueError as exc:
                raise GmailSessionError(f"{label}: response is not JSON") from exc

    # -- endpoints --------------------------------------------------------

    def list_labels(self) -> dict[str, str]:
        """Return ``{label_name: label_id}`` for every label on the account."""
        payload = self._get("labels", label="Gmail labels.list")
        labels = payload.get("labels") if isinstance(payload, dict) else None
        if not isinstance(labels, list):
            raise GmailSessionError("Gmail labels.list: unexpected payload shape")
        out: dict[str, str] = {}
        for item in labels:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            label_id = item.get("id")
            if isinstance(name, str) and isinstance(label_id, str):
                out[name] = label_id
        return out

    def list_message_ids(
        self,
        *,
        label_id: str,
        query: str | None,
        max_results: int,
    ) -> list[str]:
        """Paginate ``messages.list`` for one label; newest first (Gmail order)."""
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_results:
            page_size = min(100, max_results - len(ids))
            params: dict[str, Any] = {
                "labelIds": label_id,
                "maxResults": page_size,
                "q": query or None,
                "pageToken": page_token,
            }
            payload = self._get(
                "messages", params=params, label=f"Gmail messages.list {label_id}"
            )
            if not isinstance(payload, dict):
                raise GmailSessionError("Gmail messages.list: unexpected payload shape")
            for item in payload.get("messages") or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.append(item["id"])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return ids[:max_results]

    def get_message_raw(self, gmail_id: str) -> GmailRawMessage:
        payload = self._get(
            f"messages/{gmail_id}",
            params={"format": "raw"},
            label=f"Gmail messages.get {gmail_id}",
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("raw"), str):
            raise GmailSessionError(f"Gmail messages.get {gmail_id}: missing raw payload")
        label_ids = tuple(
            str(x) for x in (payload.get("labelIds") or []) if isinstance(x, str)
        )
        return GmailRawMessage(
            gmail_id=str(payload.get("id") or gmail_id),
            thread_id=str(payload.get("threadId") or ""),
            label_ids=label_ids,
            internal_date=_parse_internal_date(payload.get("internalDate")),
            raw=decode_raw(payload["raw"]),
            size_estimate=int(payload.get("sizeEstimate") or 0),
        )

    def get_profile(self) -> dict[str, Any]:
        payload = self._get("profile", label="Gmail profile")
        return payload if isinstance(payload, dict) else {}


def build_gmail_client(state_dir: Path) -> GmailClient:
    return GmailClient(token_path_for(state_dir))

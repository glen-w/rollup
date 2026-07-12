"""CSRF synchronizer tokens for loopback Flask forms."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from flask import Flask, session


CSRF_SESSION_KEY = "_csrf_token"


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = generate_csrf_token()
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(submitted: str | None) -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(str(expected), str(submitted))


def rotate_csrf_token() -> str:
    token = generate_csrf_token()
    session[CSRF_SESSION_KEY] = token
    return token


def init_csrf(app: Flask) -> None:
    @app.context_processor
    def _inject_csrf() -> dict[str, Any]:
        return {"csrf_token": ensure_csrf_token}

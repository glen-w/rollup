"""One-time signed maintenance confirmation tokens (server-side nonce store)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Any

_MAX_NONCES = 256
DEFAULT_TTL_SECONDS = 600

# Process-local store: nonce -> (exp, action, scope_fp, preview_fp, maint_gen)
_lock = threading.Lock()
_NONCES: dict[str, tuple[int, str, str, str, int | None]] = {}


def _sign(secret: bytes | str, payload: str) -> str:
    key = secret if isinstance(secret, bytes) else secret.encode("utf-8")
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _prune_locked(now: int) -> None:
    expired = [n for n, (exp, *_) in _NONCES.items() if exp < now]
    for n in expired:
        _NONCES.pop(n, None)
    # Bound size: drop oldest by expiry if over cap.
    if len(_NONCES) > _MAX_NONCES:
        ordered = sorted(_NONCES.items(), key=lambda kv: kv[1][0])
        for n, _ in ordered[: len(_NONCES) - _MAX_NONCES]:
            _NONCES.pop(n, None)


def issue_maintenance_token(
    *,
    secret: bytes | str,
    action: str,
    scope_fingerprint: str,
    preview_fingerprint: str,
    maintenance_generation: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Issue a single-purpose token; nonce lives in server memory only."""
    nonce = secrets.token_urlsafe(16)
    exp = int(time.time()) + int(ttl_seconds)
    body = (
        f"{action}|{scope_fingerprint}|{preview_fingerprint}|{nonce}|{exp}|"
        f"{maintenance_generation if maintenance_generation is not None else ''}"
    )
    sig = _sign(secret, body)
    token = f"{body}|{sig}"
    with _lock:
        _prune_locked(int(time.time()))
        _NONCES[nonce] = (
            exp,
            action,
            scope_fingerprint,
            preview_fingerprint,
            maintenance_generation,
        )
    return token


def consume_maintenance_token(
    token: str | None,
    *,
    secret: bytes | str,
    action: str,
    scope_fingerprint: str,
    preview_fingerprint: str,
    maintenance_generation: int | None = None,
) -> tuple[bool, str]:
    """Validate signature and atomically consume server-side nonce."""
    if not token or token.count("|") < 6:
        return False, "missing_token"
    parts = token.rsplit("|", 6)
    action_t, scope_t, preview_t, nonce, exp_s, gen_s, sig = parts
    try:
        exp = int(exp_s)
    except ValueError:
        return False, "bad_token"
    body = f"{action_t}|{scope_t}|{preview_t}|{nonce}|{exp}|{gen_s}"
    expected = _sign(secret, body)
    if not hmac.compare_digest(expected, sig):
        return False, "bad_signature"
    if action_t != action:
        return False, "wrong_action"
    if scope_t != scope_fingerprint or preview_t != preview_fingerprint:
        return False, "stale_preview"
    if int(time.time()) > exp:
        return False, "expired"
    stored_gen: int | None
    try:
        stored_gen = int(gen_s) if gen_s else None
    except ValueError:
        return False, "bad_token"
    if maintenance_generation is not None and stored_gen != maintenance_generation:
        return False, "stale_generation"
    with _lock:
        _prune_locked(int(time.time()))
        entry = _NONCES.pop(nonce, None)
        if entry is None:
            return False, "replay"
        exp_e, act_e, scope_e, prev_e, gen_e = entry
        if act_e != action or scope_e != scope_fingerprint or prev_e != preview_fingerprint:
            return False, "stale_preview"
        if gen_e != stored_gen:
            return False, "stale_generation"
        if exp_e < int(time.time()):
            return False, "expired"
    return True, "ok"


def invalidate_tokens_for_generation(maintenance_generation: int) -> None:
    """Drop pending tokens bound to a prior maintenance generation."""
    with _lock:
        drop = [
            n
            for n, (*_, gen) in _NONCES.items()
            if gen is not None and gen != maintenance_generation
        ]
        for n in drop:
            _NONCES.pop(n, None)


def fingerprint_parts(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def clear_nonce_store_for_tests() -> None:
    with _lock:
        _NONCES.clear()

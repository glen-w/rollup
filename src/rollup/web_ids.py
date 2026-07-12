"""Opaque URL encoding and authoritative message/source key validation."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from typing import Literal

# Keys are mid:/fb: or list:/from: — keep bounded for URL/path safety.
MAX_KEY_LEN = 512
_MESSAGE_KEY_RE = re.compile(r"^(mid|fb):[^\s/\\]+$")
_SOURCE_KEY_RE = re.compile(r"^(list|from):[^\s/\\]+$")
_PATH_UNSAFE = re.compile(r"[/\\]|\.\.")


class IdError(ValueError):
    """Invalid or undecodable identifier."""


def validate_message_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise IdError("empty message_key")
    if len(key) > MAX_KEY_LEN:
        raise IdError("message_key too long")
    if _PATH_UNSAFE.search(key) or not _MESSAGE_KEY_RE.match(key):
        raise IdError(f"invalid message_key: {key!r}")
    return key


def validate_source_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise IdError("empty source_key")
    if len(key) > MAX_KEY_LEN:
        raise IdError("source_key too long")
    if _PATH_UNSAFE.search(key) or not _SOURCE_KEY_RE.match(key):
        raise IdError(f"invalid source_key: {key!r}")
    return key


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id.strip():
        raise IdError("empty run_id")
    try:
        uuid.UUID(run_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise IdError(f"invalid run_id: {run_id!r}") from exc
    return run_id


def encode_opaque(key: str) -> str:
    """URL-safe base64 without padding."""
    raw = key.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def encode_run_opaque(run_id: str) -> str:
    return encode_opaque(validate_run_id(run_id))


def decode_run_opaque(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise IdError("empty opaque run id")
    if len(token) > 1024:
        raise IdError("opaque run id too long")
    if "/" in token or "\\" in token or ".." in token:
        raise IdError("opaque run id contains path characters")
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        key = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise IdError("malformed opaque run id") from exc
    return validate_run_id(key)


def decode_opaque(token: str, *, kind: Literal["message", "source"]) -> str:
    if not isinstance(token, str) or not token:
        raise IdError("empty opaque id")
    if len(token) > 1024:
        raise IdError("opaque id too long")
    if "/" in token or "\\" in token or ".." in token:
        raise IdError("opaque id contains path characters")
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        key = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise IdError("malformed opaque id") from exc
    if kind == "message":
        return validate_message_key(key)
    return validate_source_key(key)

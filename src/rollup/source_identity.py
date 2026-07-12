"""Canonical newsletter source identity (List-ID-first, then From)."""

from __future__ import annotations

import re
import unicodedata
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Any

MAX_SOURCE_KEY_LEN = 256
MAX_LIST_ID_LEN = 200

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_UNKNOWN_ADDRS = frozenset({"", "(unknown)", "unknown"})


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _has_control_chars(text: str) -> bool:
    return bool(_CONTROL_RE.search(text))


def _clip_key(key: str) -> str:
    if len(key) <= MAX_SOURCE_KEY_LEN:
        return key
    return key[:MAX_SOURCE_KEY_LEN]


def normalize_email(from_header: str) -> str:
    """Lowercase email address from a From header (legacy grouping helper)."""
    _, addr = parseaddr(from_header or "")
    addr = (addr or from_header or "").strip().lower()
    if "<" in addr and ">" in addr:
        addr = addr[addr.find("<") + 1 : addr.find(">")].strip()
    return addr


def normalize_from_addr(from_header: str | None) -> str | None:
    """Return a durable From address, or None if unusable for identity."""
    try:
        if not from_header or not str(from_header).strip():
            return None
        decoded = _decode_header_value(str(from_header))
        if _has_control_chars(decoded):
            return None
        addr = normalize_email(decoded)
        if not addr or addr in _UNKNOWN_ADDRS or "@" not in addr:
            return None
        if _has_control_chars(addr):
            return None
        local, _, domain = addr.partition("@")
        if not local or not domain or "." not in domain and domain != "localhost":
            # Allow localhost for tests; still require @ shape.
            if not local or not domain:
                return None
        return addr
    except Exception:
        return None


def normalize_list_id(raw: str | None) -> str | None:
    """Hardened List-ID normalisation. Never raises; returns None on failure."""
    try:
        if raw is None:
            return None
        text = _decode_header_value(str(raw)).strip()
        if not text:
            return None
        text = unicodedata.normalize("NFC", text)
        if _has_control_chars(text):
            return None
        if "<" in text and ">" in text:
            inner = text[text.find("<") + 1 : text.find(">")].strip()
            text = inner if inner else text
        else:
            parts = text.split()
            text = parts[-1] if parts else text
        text = text.strip().lower().rstrip(".")
        if not text or text == "localhost":
            return None
        if len(text) > MAX_LIST_ID_LEN:
            return None
        if _has_control_chars(text) or not any(c.isalnum() for c in text):
            return None
        return text
    except Exception:
        return None


def _first_header(headers: Any, *names: str) -> str | None:
    """Return the first present header value from a mapping or email message."""
    for name in names:
        try:
            if hasattr(headers, "get_all"):
                values = headers.get_all(name) or headers.get_all(name.lower())
                if values:
                    for value in values:
                        if value is not None and str(value).strip():
                            return str(value)
            value = headers.get(name) if hasattr(headers, "get") else None
            if value is None and hasattr(headers, "get"):
                value = headers.get(name.lower())
            if value is not None and str(value).strip():
                return str(value)
        except Exception:
            continue
    return None


def compute_source_key(
    headers: Any | None = None,
    *,
    list_id_header: str | None = None,
    from_header: str | None = None,
) -> str | None:
    """Return canonical source_key or None when unidentifiable.

    Prefer List-ID, else normalised From. Never synthesises a shared key from
    empty From. Never uses Subject / Reply-To / Return-Path / Sender.
    """
    try:
        raw_list = list_id_header
        if raw_list is None and headers is not None:
            raw_list = _first_header(headers, "List-ID", "List-Id", "list-id")
        lid = normalize_list_id(raw_list)
        if lid:
            return _clip_key(f"list:{lid}")

        raw_from = from_header
        if raw_from is None and headers is not None:
            raw_from = _first_header(headers, "From", "from")
        addr = normalize_from_addr(raw_from)
        if addr:
            return _clip_key(f"from:{addr}")
        return None
    except Exception:
        return None


def extract_display_name(from_header: str | None) -> str | None:
    """Best-effort display name from a From header (no address)."""
    if not from_header:
        return None
    try:
        decoded = _decode_header_value(str(from_header))
        name, _addr = parseaddr(decoded)
        name = (name or "").strip()
        if not name or _has_control_chars(name):
            return None
        return name
    except Exception:
        return None


def validate_display_name_override(value: str) -> str:
    """Validate user display-name override; raise ValueError if blank/control."""
    text = (value or "").strip()
    if not text:
        raise ValueError("display_name override must be non-empty")
    if _has_control_chars(text):
        raise ValueError("display_name override must not contain control characters")
    return text

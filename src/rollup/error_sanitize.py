"""Redact secret-like substrings from provider error messages."""

from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
)


def sanitize_provider_message(message: str) -> str:
    """Return *message* with likely API keys/tokens redacted."""
    out = message
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out

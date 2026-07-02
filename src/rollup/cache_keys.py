"""Canonical cache identity helpers for summary generations."""

from __future__ import annotations

import json
from typing import Any


def canonicalize_provider_options(options: dict[str, Any] | None) -> str:
    """Return stable JSON for provider option identity."""
    return json.dumps(options or {}, sort_keys=True, separators=(",", ":"))

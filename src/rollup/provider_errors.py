"""Named provider/transport exceptions for LLM call sites.

Catch policy at model-call boundaries:
  - Treat requests.RequestException, json.JSONDecodeError, and UnicodeDecodeError
    as provider failures (degrade / fallback).
  - Do not convert TypeError/AttributeError/other programming faults.
  - Never convert KeyboardInterrupt, SystemExit, GeneratorExit, or
    asyncio.CancelledError (they are BaseException and bypass ``except Exception``).

``requests`` is imported lazily so the default no-network digest path does not
pull it in at module import time.
"""

from __future__ import annotations

import json

PROVIDER_PAYLOAD_EXCEPTIONS: tuple[type[BaseException], ...] = (
    json.JSONDecodeError,
    UnicodeDecodeError,
)


def is_provider_call_error(exc: BaseException) -> bool:
    """Return True if *exc* is a named provider transport/payload failure."""
    if isinstance(exc, PROVIDER_PAYLOAD_EXCEPTIONS):
        return True
    import requests

    return isinstance(exc, requests.RequestException)

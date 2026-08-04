"""Provider transport/payload error classification."""

from __future__ import annotations

import json

from rollup.provider_errors import is_provider_call_error


def test_json_decode_is_provider_error() -> None:
    try:
        json.loads("{")
    except json.JSONDecodeError as exc:
        assert is_provider_call_error(exc)


def test_unicode_decode_is_provider_error() -> None:
    try:
        b"\xff".decode("utf-8")
    except UnicodeDecodeError as exc:
        assert is_provider_call_error(exc)


def test_type_error_is_not_provider_error() -> None:
    assert not is_provider_call_error(TypeError("bug"))


def test_requests_exception_is_provider_error() -> None:
    import requests

    assert is_provider_call_error(requests.ConnectionError("down"))

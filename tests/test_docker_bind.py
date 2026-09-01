"""Docker port-mapping bind flag (rollup web)."""

from __future__ import annotations

import pytest

from rollup.cli_parser import build_parser
from rollup.web.bind import BindError, validate_bind_host


def test_web_parser_exposes_allow_non_loopback_bind() -> None:
    parser = build_parser()
    args = parser.parse_args(["web", "--allow-non-loopback-bind", "--host", "0.0.0.0"])
    assert args.command == "web"
    assert args.allow_non_loopback_bind is True
    assert args.host == "0.0.0.0"


def test_web_parser_default_disallows_wildcard_bind_at_runtime() -> None:
    parser = build_parser()
    args = parser.parse_args(["web", "--host", "0.0.0.0"])
    assert args.allow_non_loopback_bind is False
    with pytest.raises(BindError):
        validate_bind_host(args.host, allow_non_loopback=args.allow_non_loopback_bind)


def test_validate_bind_host_bracketed_ipv6_wildcard_opt_in() -> None:
    assert validate_bind_host("[::]", allow_non_loopback=True) == "[::]"


def test_validate_bind_host_still_rejects_public_hostname() -> None:
    with pytest.raises(BindError):
        validate_bind_host("example.com", allow_non_loopback=True)

"""Loopback bind validation for rollup web."""

from __future__ import annotations

import ipaddress
import socket


class BindError(ValueError):
    pass


def is_loopback_host(host: str) -> bool:
    text = (host or "").strip().lower()
    if not text:
        return False
    if text in {"localhost", "127.0.0.1", "::1"}:
        return True
    # Bracketed IPv6
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BindError(f"cannot resolve host {host!r}: {exc}") from exc
    if not infos:
        raise BindError(f"cannot resolve host {host!r}")
    for info in infos:
        addr = info[4][0]
        try:
            if not ipaddress.ip_address(addr).is_loopback:
                return False
        except ValueError:
            return False
    return True


def validate_bind_host(host: str) -> str:
    text = (host or "").strip()
    if text in {"0.0.0.0", "::", "[::]"}:
        raise BindError(f"refusing non-loopback bind address {text!r}")
    if not is_loopback_host(text):
        raise BindError(
            f"refusing non-loopback host {text!r}; rollup web is loopback-only"
        )
    return text

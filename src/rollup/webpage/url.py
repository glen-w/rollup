"""HTTPS URL validation, canonicalization, and SSRF guards for webpage queue."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import urldefrag, urlparse, urlunparse

WEBPAGE_URL_ERROR = "webpage_url_invalid"
WEBPAGE_URL_SSRF = "webpage_url_ssrf"


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def message_key_for_url(canonical_url: str) -> str:
    return f"web:url:{url_hash(canonical_url)}"


def source_key_for_url(canonical_url: str) -> str | None:
    try:
        parsed = urlparse(canonical_url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    return f"web:host:{host[:200]}"


def canonicalize_https_url(raw: str) -> str:
    """Normalize user URL to canonical https form. Raises ValueError on invalid."""
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("URL must not be empty")
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("URLs with credentials are not allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL must include a host")
    port = parsed.port
    if port is not None and port not in (80, 443):
        raise ValueError("Non-standard ports are not allowed")
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    # strip fragment
    no_frag, _ = urldefrag(
        urlunparse(("https", host, path, "", parsed.query, ""))
    )
    return no_frag.rstrip("/") if no_frag.endswith("/") and path != "/" else no_frag


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        addr.is_loopback
        or addr.is_link_local
        or addr.is_private
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _blocked_metadata_host(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered in ("localhost", "metadata.google.internal"):
        return True
    if lowered.endswith(".local") or lowered.endswith(".internal"):
        return True
    return False


def normalize_redirect_url(raw: str) -> str:
    """Normalize a redirect Location for fetch; preserve trailing slash on path."""
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("Redirect URL must not be empty")
    if cleaned.startswith("/"):
        raise ValueError("Relative redirects are not supported")
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    parsed = urlparse(cleaned)
    if parsed.scheme != "https":
        raise ValueError("Redirect must use https")
    if parsed.username or parsed.password:
        raise ValueError("Redirects with credentials are not allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("Redirect must include a host")
    port = parsed.port
    if port is not None and port not in (443,):
        raise ValueError("Non-standard redirect ports are not allowed")
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    no_frag, _ = urldefrag(
        urlunparse(("https", host, path, "", parsed.query, ""))
    )
    assert_safe_fetch_host(host)
    return no_frag


def assert_safe_fetch_host(host: str) -> None:
    """Raise ValueError if host resolves to a blocked address (SSRF guard)."""
    if _blocked_metadata_host(host):
        raise ValueError(f"Blocked host: {host}")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host {host}") from exc
    if not infos:
        raise ValueError(f"Cannot resolve host {host}")
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked_ip(addr):
            raise ValueError(f"Blocked address for {host}: {ip_str}")


def validate_queue_url(raw: str) -> str:
    """Canonicalize and SSRF-check a queue URL. Returns canonical https URL."""
    canonical = canonicalize_https_url(raw)
    host = urlparse(canonical).hostname
    if host is None:
        raise ValueError("URL must include a host")
    assert_safe_fetch_host(host)
    return canonical

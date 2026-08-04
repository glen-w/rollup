"""Response security headers and Host validation for the local web UI."""

from __future__ import annotations

from flask import Flask, Request, Response, abort, request

from rollup.web.bind import BindError, is_loopback_host


def _normalise_host_header(raw: str) -> tuple[str, str | None]:
    """Split Host into hostname and optional port; handle bracketed IPv6."""
    text = (raw or "").strip().lower()
    if not text:
        return "", None
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return text, None
        host = text[1:end]
        rest = text[end + 1 :]
        port = rest[1:] if rest.startswith(":") else None
        return host, port
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        return host, port
    return text, None


def trusted_host_allowed(req: Request, *, bind_host: str, bind_port: int) -> bool:
    """Allow only the configured loopback Host forms; ignore forwarded-host."""
    # Intentionally ignore X-Forwarded-Host / Forwarded.
    raw = req.headers.get("Host") or ""
    host, port_s = _normalise_host_header(raw)
    if not host:
        return False
    try:
        loopback = is_loopback_host(host)
    except BindError:
        return False
    if not loopback:
        return False
    bind = (bind_host or "").strip().lower()
    if bind.startswith("[") and bind.endswith("]"):
        bind = bind[1:-1]
    allowed_hosts = {bind, "127.0.0.1", "localhost", "::1"}
    if bind == "127.0.0.1":
        allowed_hosts.add("localhost")
    if host not in allowed_hosts:
        return False
    if port_s is None:
        return True
    try:
        return int(port_s) == int(bind_port)
    except ValueError:
        return False


def init_security_headers(app: Flask) -> None:
    @app.before_request
    def _validate_host() -> None:
        if app.config.get("TESTING"):
            # Tests use Flask test client Host=localhost by default; still require
            # loopback, but allow any loopback host without strict port match when
            # WEB_ENFORCE_HOST is false.
            if not app.config.get("WEB_ENFORCE_HOST", False):
                raw = (request.headers.get("Host") or "").split(":")[0]
                if raw.startswith("["):
                    raw = raw.strip("[]")
                if raw and not is_loopback_host(raw):
                    abort(400)
                return
        bind_host = str(app.config.get("WEB_BIND_HOST", "127.0.0.1"))
        bind_port = int(app.config.get("WEB_BIND_PORT", 8765))
        if not trusted_host_allowed(
            request, bind_host=bind_host, bind_port=bind_port
        ):
            abort(400)

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; "
            "form-action 'self'",
        )
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        # Global no-store for every response class (success, errors, redirects).
        resp.headers["Cache-Control"] = "private, no-store"
        resp.headers["Pragma"] = "no-cache"
        return resp

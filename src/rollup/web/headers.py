"""Response security headers for the local web UI."""

from __future__ import annotations

from flask import Flask, Response


def init_security_headers(app: Flask) -> None:
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
        return resp

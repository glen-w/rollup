"""Route-enumerating web invariants.

Instead of asserting hardening per handler, these tests walk ``app.url_map``
so a newly added route cannot opt out:

* every state-changing route rejects a request without a valid CSRF token
  (or, for the extension capture endpoint, without the Bearer token) with a
  4xx and never 2xx/3xx;
* every route refuses a non-loopback ``Host`` before dispatch;
* every response carries the security headers and no-store cache policy;
* oversize request bodies are refused by the app-wide content cap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("flask")

from rollup.state import init_db
from rollup.web.app import create_app

SECURITY_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

# Routes whose POST authenticates with the extension Bearer token instead of CSRF.
BEARER_ROUTES = {"/articles/capture"}


def _placeholder(rule) -> str:
    """Fill URL converters with syntactically plausible values."""
    path = rule.rule
    for arg in rule.arguments:
        converter = rule._converters[arg]
        name = type(converter).__name__.lower()
        if "int" in name:
            value = "1"
        elif "uuid" in name:
            value = "00000000-0000-4000-8000-000000000000"
        else:
            value = "placeholder"
        path = re.sub(rf"<[^<>]*:?{arg}>", value, path)
    return path


@pytest.fixture
def app(tmp_path: Path):
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    init_db(state / "rollup.db").close()
    return create_app(state_dir=state, output_dir=out, testing=True)


def _mutating_rules(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        methods = {m for m in (rule.methods or set()) if m not in ("HEAD", "OPTIONS")}
        if methods & {"POST", "PUT", "PATCH", "DELETE"}:
            yield rule, sorted(methods & {"POST", "PUT", "PATCH", "DELETE"})


def _get_rules(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        if "GET" in (rule.methods or set()):
            yield rule


def test_there_are_mutating_routes(app) -> None:
    assert list(_mutating_rules(app)), "expected at least one POST route to protect"


def test_every_mutating_route_rejects_missing_credentials(app) -> None:
    client = app.test_client()
    failures: list[str] = []
    for rule, methods in _mutating_rules(app):
        path = _placeholder(rule)
        for method in methods:
            resp = client.open(path, method=method, data={})
            if resp.status_code < 400:
                failures.append(f"{method} {path} -> {resp.status_code}")
    assert not failures, "unauthenticated mutation accepted:\n" + "\n".join(failures)


def test_every_mutating_route_rejects_forged_csrf(app) -> None:
    client = app.test_client()
    failures: list[str] = []
    for rule, methods in _mutating_rules(app):
        path = _placeholder(rule)
        if path in BEARER_ROUTES:
            continue
        for method in methods:
            resp = client.open(path, method=method, data={"csrf_token": "forged" * 8})
            if resp.status_code < 400:
                failures.append(f"{method} {path} -> {resp.status_code}")
    assert not failures, "forged CSRF accepted:\n" + "\n".join(failures)


def test_capture_rejects_bad_and_missing_bearer(app) -> None:
    client = app.test_client()
    body = {"url": "https://example.com/article", "title": "x"}
    assert client.post("/articles/capture", json=body).status_code in (401, 403)
    assert (
        client.post(
            "/articles/capture", json=body, headers={"Authorization": "Bearer nope"}
        ).status_code
        in (401, 403)
    )
    assert (
        client.post(
            "/articles/capture", json=body, headers={"Authorization": "Basic abc"}
        ).status_code
        in (401, 403)
    )


def test_every_route_refuses_non_loopback_host(app) -> None:
    client = app.test_client()
    failures: list[str] = []
    for rule in _get_rules(app):
        path = _placeholder(rule)
        for host in ("evil.example.com", "10.0.0.5", "rollup.internal:8765"):
            resp = client.get(path, headers={"Host": host})
            if resp.status_code != 400:
                failures.append(f"GET {path} Host={host} -> {resp.status_code}")
    assert not failures, "non-loopback Host served:\n" + "\n".join(failures)


def test_forwarded_host_headers_are_ignored(app) -> None:
    client = app.test_client()
    # Forwarded headers must not grant trust…
    resp = client.get(
        "/rollups",
        headers={"Host": "evil.example.com", "X-Forwarded-Host": "127.0.0.1"},
    )
    assert resp.status_code == 400
    # …and must not revoke it either.
    resp = client.get(
        "/rollups", headers={"Host": "127.0.0.1", "X-Forwarded-Host": "evil.example.com"}
    )
    assert resp.status_code != 400


def test_every_response_carries_security_headers(app) -> None:
    client = app.test_client()
    failures: list[str] = []
    for rule in _get_rules(app):
        path = _placeholder(rule)
        resp = client.get(path)
        for header, needle in SECURITY_HEADERS.items():
            if needle not in (resp.headers.get(header) or ""):
                failures.append(f"GET {path} ({resp.status_code}) missing {header}")
        if resp.headers.get("Cache-Control") != "private, no-store":
            failures.append(f"GET {path} ({resp.status_code}) cacheable")
    # Error responses included: 404s and 400s must be hardened too.
    resp = client.get("/definitely-not-a-route")
    assert resp.status_code == 404
    assert "DENY" == resp.headers.get("X-Frame-Options")
    assert not failures, "\n".join(failures)


def test_csp_forbids_inline_and_remote_script(app) -> None:
    client = app.test_client()
    csp = client.get("/rollups").headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "form-action 'self'" in csp


def test_oversize_body_is_refused_app_wide(app) -> None:
    client = app.test_client()
    cap = int(app.config["MAX_CONTENT_LENGTH"])
    blob = b"a" * (cap + 1)
    resp = client.post(
        "/articles/capture",
        data=blob,
        headers={
            "Authorization": f"Bearer {app.config['EXTENSION_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 413


def test_get_routes_do_not_mutate_the_database(app) -> None:
    """GET is read-only: the DB file is byte-identical after walking every GET route."""
    db = Path(app.config["STATE_DIR"]) / "rollup.db"
    import sqlite3

    def snapshot() -> list[tuple]:
        conn = sqlite3.connect(db)
        try:
            rows: list[tuple] = []
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ):
                if name.startswith("sqlite_") or name.endswith("_fts") or "_fts_" in name:
                    continue
                rows.append((name, conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]))
            return rows
        finally:
            conn.close()

    before = snapshot()
    client = app.test_client()
    for rule in _get_rules(app):
        client.get(_placeholder(rule))
    assert snapshot() == before

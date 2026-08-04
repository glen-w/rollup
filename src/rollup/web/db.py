"""Web database connection helpers: read-only GET vs short-lived POST mutators."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from flask import abort, current_app, g

from rollup.state import (
    SchemaCompatibilityError,
    assert_schema_readable,
    connect_db_mutator,
    connect_db_readonly,
)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    conn = connect_db_readonly(db_path)
    assert_schema_readable(conn)
    return conn


def open_mutator(db_path: Path) -> sqlite3.Connection:
    conn = connect_db_mutator(db_path)
    assert_schema_readable(conn)
    return conn


@contextmanager
def mutation_connection() -> Iterator[sqlite3.Connection]:
    """Short-lived write connection for one POST mutation after CSRF/validation.

    Caller owns the transaction. Never open this from the request hook.
    """
    path = Path(current_app.config["DB_PATH"])
    try:
        conn = open_mutator(path)
    except FileNotFoundError:
        abort(503)
    except SchemaCompatibilityError:
        abort(400)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            abort(503)
        raise
    g.db_write = conn
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
        if getattr(g, "db_write", None) is conn:
            g.db_write = None


def require_ro() -> sqlite3.Connection:
    conn = getattr(g, "db_ro", None)
    if conn is None:
        raise RuntimeError("read-only database connection is not open")
    return conn

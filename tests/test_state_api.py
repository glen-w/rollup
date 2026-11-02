"""Public state import surface stays on rollup.state after the package split."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup import state
from rollup.state import (
    SCHEMA_VERSION,
    SOURCE_REGISTRY_SCHEMA_V7,
    SchemaCompatibilityError,
    apply_connection_pragmas,
    assert_schema_readable,
    connect_db,
    ensure_source_registry_schema,
    get_schema_version,
    init_db,
    run_schema_migrations,
)
from rollup.state import migrations as state_migrations
from rollup.state import schema as state_schema


def test_public_names_are_package_reexports() -> None:
    assert SCHEMA_VERSION is state_schema.SCHEMA_VERSION
    assert SCHEMA_VERSION == 16
    assert SOURCE_REGISTRY_SCHEMA_V7 is state_schema.SOURCE_REGISTRY_SCHEMA_V7
    assert apply_connection_pragmas is state.apply_connection_pragmas
    assert init_db is state.init_db
    assert connect_db is state.connect_db
    assert get_schema_version is state_migrations.get_schema_version
    assert run_schema_migrations is state_migrations.run_schema_migrations
    assert ensure_source_registry_schema is state_migrations.ensure_source_registry_schema
    assert state._ensure_schema_version_singleton is state_migrations._ensure_schema_version_singleton
    assert state._migrate_schema_version_singleton is state_migrations._ensure_schema_version_singleton


def test_init_db_uses_schema_package(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION
    assert assert_schema_readable(conn) == SCHEMA_VERSION
    conn.close()


def test_assert_schema_readable_fail_closed(tmp_path: Path) -> None:
    empty = connect_db(tmp_path / "empty.db")
    with pytest.raises(SchemaCompatibilityError, match="no schema_version"):
        assert_schema_readable(empty)
    empty.close()

    old = connect_db(tmp_path / "old.db")
    old.executescript(
        """
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, 7);
        """
    )
    old.commit()
    with pytest.raises(SchemaCompatibilityError, match="too old"):
        assert_schema_readable(old)
    old.close()

    future = connect_db(tmp_path / "future.db")
    future.executescript(
        f"""
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, {SCHEMA_VERSION + 1});
        """
    )
    future.commit()
    with pytest.raises(SchemaCompatibilityError, match="unsupported schema"):
        assert_schema_readable(future)
    future.close()

"""Schema v17 FTS corpus search."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.cli import build_parser
from rollup.corpus import (
    corpus_table_exists,
    fts5_available,
    rebuild_corpus,
    search_corpus,
    upsert_corpus_row,
)
from rollup.corpus_cmd import cmd_search
from rollup.state import (
    SCHEMA_VERSION,
    connect_db,
    ensure_message_corpus_fts_v17,
    get_schema_version,
    init_db,
)
from rollup.utc import format_utc


pytestmark = pytest.mark.skipif(
    not fts5_available(),
    reason="SQLite FTS5 is not available in this build",
)


def _insert_body(conn, key: str, body: str) -> None:
    now = format_utc(datetime(2026, 7, 2, tzinfo=timezone.utc))
    conn.execute(
        """INSERT INTO message_reader_bodies (
            message_key, content_hash, stored_body_hash, body_text, truncated,
            updated_at, last_seen_at, reader_text_version, source_body_length,
            reader_hash_authoritative
           ) VALUES (?, ?, ?, ?, 0, ?, ?, 2, ?, 1)""",
        (key, "a" * 64, "b" * 64, body, now, now, len(body)),
    )


def test_fresh_db_has_fts_table(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION == 17
    assert corpus_table_exists(conn)
    conn.close()


def test_v16_to_v17_rebuilds_existing_bodies(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    _insert_body(conn, "mid:local@ex.com", "A dispatch about xylophone practice")
    conn.commit()
    conn.execute("DROP TABLE message_corpus_fts")
    conn.execute("UPDATE schema_version SET version = 16 WHERE id = 1")
    conn.commit()
    assert get_schema_version(conn) == 16
    assert not corpus_table_exists(conn)
    conn.close()

    conn = connect_db(db)
    ensure_message_corpus_fts_v17(conn)
    assert get_schema_version(conn) == 17
    hits = search_corpus(conn, "xylophone")
    assert len(hits) == 1
    assert hits[0].message_key == "mid:local@ex.com"
    conn.close()


def test_search_ranks_matching_body(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    upsert_corpus_row(
        conn,
        message_key="mid:a@x",
        title="Local-first evals shipped",
        body_text="Local-first evaluation harness notes",
    )
    upsert_corpus_row(
        conn,
        message_key="mid:b@x",
        title="Weekly grocery list",
        body_text="Milk eggs bread",
    )
    conn.commit()
    hits = search_corpus(conn, "local-first evals")
    assert hits
    assert hits[0].message_key == "mid:a@x"
    grocery = search_corpus(conn, "grocery")
    assert [h.message_key for h in grocery] == ["mid:b@x"]
    assert search_corpus(conn, "no-such-token-zzz") == ()
    conn.close()


def test_rebuild_from_bodies(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    _insert_body(conn, "mid:body@x", "Cached body mentions zephyr")
    n = rebuild_corpus(conn)
    conn.commit()
    assert n >= 1
    keys = {h.message_key for h in search_corpus(conn, "zephyr")}
    assert "mid:body@x" in keys
    conn.close()


def test_search_cli_json(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    state.mkdir()
    conn = init_db(state / "rollup.db")
    upsert_corpus_row(
        conn,
        message_key="mid:cli@x",
        title="Helium balloon briefing",
        body_text="Helium notes",
    )
    conn.commit()
    conn.close()
    args = build_parser().parse_args(
        ["search", "helium", "--state-dir", str(state), "--json"]
    )
    assert cmd_search(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["hits"][0]["message_key"] == "mid:cli@x"


def test_search_cli_empty_query(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    args = build_parser().parse_args(["search", "--state-dir", str(state), "   "])
    # argparse still captures whitespace tokens; cmd_search strips them.
    args.query = ["  "]
    assert cmd_search(args) == 2

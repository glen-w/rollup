"""Tests for output_dir archive of prior digest batches."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rollup.output_archive import (
    ARCHIVE_DIRNAME,
    archive_previous_outputs,
    is_archivable_output,
    resolve_output_artifact,
)
from rollup.publication import LATEST_HTML, LATEST_MD


def test_is_archivable_output(tmp_path: Path) -> None:
    digest = tmp_path / "2026-08-04T120000Z-abcd1234-newsletter-digest.md"
    digest.write_text("x", encoding="utf-8")
    assert is_archivable_output(digest)

    xteink = tmp_path / "2026-08-04T120000Z-abcd1234-newsletter-digest.xteink.md"
    xteink.write_text("x", encoding="utf-8")
    assert is_archivable_output(xteink)

    latest = tmp_path / LATEST_MD
    latest.write_text("x", encoding="utf-8")
    assert not is_archivable_output(latest)

    logo = tmp_path / "rollup_logo.png"
    logo.write_bytes(b"png")
    assert not is_archivable_output(logo)

    tmp = tmp_path / ".tmp-stem.md"
    tmp.write_text("x", encoding="utf-8")
    assert not is_archivable_output(tmp)


def test_archive_moves_prior_batch_keeps_latest_and_branding(tmp_path: Path) -> None:
    old_md = tmp_path / "2026-08-01T100000Z-aaaaaaaa-newsletter-digest.md"
    old_html = tmp_path / "2026-08-01T100000Z-aaaaaaaa-newsletter-digest.html"
    old_json = tmp_path / "2026-08-01T100000Z-aaaaaaaa-newsletter-digest.json"
    old_md.write_text("# old", encoding="utf-8")
    old_html.write_text("<html>old</html>", encoding="utf-8")
    old_json.write_text("{}", encoding="utf-8")

    (tmp_path / LATEST_MD).write_text("# latest", encoding="utf-8")
    (tmp_path / LATEST_HTML).write_text("<html>latest</html>", encoding="utf-8")
    (tmp_path / "rollup_logo.png").write_bytes(b"logo")
    (tmp_path / "favicon.ico").write_bytes(b"ico")
    (tmp_path / "notes.txt").write_text("keep me", encoding="utf-8")

    moved = archive_previous_outputs(tmp_path)
    assert len(moved) == 3
    archive = tmp_path / ARCHIVE_DIRNAME
    assert (archive / old_md.name).is_file()
    assert (archive / old_html.name).is_file()
    assert (archive / old_json.name).is_file()
    assert not old_md.exists()
    assert (tmp_path / LATEST_MD).read_text(encoding="utf-8") == "# latest"
    assert (tmp_path / "rollup_logo.png").is_file()
    assert (tmp_path / "notes.txt").is_file()


def test_archive_empty_when_nothing_to_move(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / LATEST_MD).write_text("x", encoding="utf-8")
    assert archive_previous_outputs(tmp_path) == []
    assert not (tmp_path / ARCHIVE_DIRNAME).exists()


def test_resolve_output_artifact_falls_back_to_archive(tmp_path: Path) -> None:
    name = "2026-08-01T100000Z-aaaaaaaa-newsletter-digest.md"
    archived = tmp_path / ARCHIVE_DIRNAME / name
    archived.parent.mkdir()
    archived.write_text("# archived", encoding="utf-8")

    found = resolve_output_artifact(tmp_path, name)
    assert found == archived.resolve()

    # Prefer root when both exist.
    root_copy = tmp_path / name
    root_copy.write_text("# root", encoding="utf-8")
    found_root = resolve_output_artifact(tmp_path, name)
    assert found_root == root_copy.resolve()

    assert resolve_output_artifact(tmp_path, "missing.md") is None
    assert resolve_output_artifact(tmp_path, "../escape.md") is None


def test_archive_rewrites_indexed_relpaths(tmp_path: Path) -> None:
    name_md = "2026-08-01T100000Z-aaaaaaaa-newsletter-digest.md"
    name_html = "2026-08-01T100000Z-aaaaaaaa-newsletter-digest.html"
    (tmp_path / name_md).write_text("# old", encoding="utf-8")
    (tmp_path / name_html).write_text("<html></html>", encoding="utf-8")

    db_path = tmp_path / "rollup.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE rollup_runs (
             run_id TEXT PRIMARY KEY,
             markdown_relpath TEXT,
             html_relpath TEXT
           )"""
    )
    conn.execute(
        "INSERT INTO rollup_runs VALUES (?, ?, ?)",
        ("run-1", name_md, name_html),
    )
    conn.commit()
    conn.close()

    archive_previous_outputs(tmp_path, db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT markdown_relpath, html_relpath FROM rollup_runs WHERE run_id = ?",
        ("run-1",),
    ).fetchone()
    conn.close()
    assert row == (f"{ARCHIVE_DIRNAME}/{name_md}", f"{ARCHIVE_DIRNAME}/{name_html}")
    assert resolve_output_artifact(tmp_path, row[0]).is_file()

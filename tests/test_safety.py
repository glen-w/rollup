"""Tests for safety guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.safety import (
    SafetyError,
    assert_safe_write_paths,
    is_inside,
    validate_read_root,
)


def test_is_inside_child_in_parent(tmp_path: Path) -> None:
    parent = tmp_path / "mail"
    child = parent / "subdir"
    parent.mkdir()
    child.mkdir()
    assert is_inside(child, parent)


def test_is_inside_equal_paths(tmp_path: Path) -> None:
    p = tmp_path / "mail"
    p.mkdir()
    assert is_inside(p, p)


def test_is_inside_outside(tmp_path: Path) -> None:
    mail = tmp_path / "mail"
    other = tmp_path / "other"
    mail.mkdir()
    other.mkdir()
    assert not is_inside(other, mail)


def test_assert_safe_write_paths_rejects_inside_mail_root(tmp_path: Path) -> None:
    mail_root = tmp_path / "gmail"
    mail_root.mkdir()
    bad_output = mail_root / "output"
    with pytest.raises(SafetyError, match="inside mail root"):
        assert_safe_write_paths(mail_root, bad_output)


def test_assert_safe_write_paths_allows_outside(tmp_path: Path) -> None:
    mail_root = tmp_path / "gmail"
    mail_root.mkdir()
    output = tmp_path / "output"
    assert_safe_write_paths(mail_root, output)


def test_symlink_resolved_into_mail_root(tmp_path: Path) -> None:
    mail_root = tmp_path / "gmail"
    mail_root.mkdir()
    inside = mail_root / "output"
    inside.mkdir()
    link_outside = tmp_path / "evil_link"
    try:
        link_outside.symlink_to(inside)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(SafetyError):
        assert_safe_write_paths(mail_root, link_outside)


def test_validate_read_root_missing(tmp_path: Path) -> None:
    with pytest.raises(SafetyError, match="does not exist"):
        validate_read_root(
            tmp_path / "nope",
            tmp_path / "mail",
            tmp_path / "out",
            tmp_path / "state",
            tmp_path / "logs",
        )


def test_validate_read_root_not_directory(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(SafetyError, match="not a directory"):
        validate_read_root(f, tmp_path, tmp_path, tmp_path, tmp_path)


def test_validate_read_root_equals_output_dir(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    with pytest.raises(SafetyError, match="must not equal output_dir"):
        validate_read_root(root, tmp_path, root, tmp_path / "state", tmp_path / "logs")


def test_validate_read_root_equals_state_dir(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    with pytest.raises(SafetyError, match="must not equal state_dir"):
        validate_read_root(
            root,
            tmp_path / "mail",
            tmp_path / "out",
            root,
            tmp_path / "logs",
        )


@pytest.mark.parametrize(
    "bad_path_name",
    [
        "output",
        "state",
        "rollup.db",
        "digest.md",
        "digest.html",
        "tmp.md",
        "inventory.json",
    ],
)
def test_assert_safe_write_paths_rejects_various_inside_paths(
    tmp_path: Path, bad_path_name: str
) -> None:
    mail_root = tmp_path / "gmail"
    mail_root.mkdir()
    mapping = {
        "output": mail_root / "output",
        "state": mail_root / "state",
        "rollup.db": mail_root / "rollup.db",
        "digest.md": mail_root / "2026-07-01-newsletter-digest.md",
        "digest.html": mail_root / "2026-07-01-newsletter-digest.html",
        "tmp.md": mail_root / ".tmp-2026-07-01-newsletter-digest.md",
        "inventory.json": mail_root / "inventory.json",
    }
    bad = mapping[bad_path_name]
    bad.parent.mkdir(parents=True, exist_ok=True)
    if bad_path_name.endswith(".db") or bad_path_name.endswith(".json"):
        bad.touch()
    with pytest.raises(SafetyError, match="inside mail root"):
        assert_safe_write_paths(mail_root, bad)


def test_symlink_state_dir_into_mail_root(tmp_path: Path) -> None:
    mail_root = tmp_path / "gmail"
    mail_root.mkdir()
    inside = mail_root / "state"
    inside.mkdir()
    link = tmp_path / "state_link"
    try:
        link.symlink_to(inside)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(SafetyError):
        assert_safe_write_paths(mail_root, link / "rollup.db")


def test_symlink_json_out_into_mail_root(tmp_path: Path) -> None:
    mail_root = tmp_path / "gmail"
    mail_root.mkdir()
    inside = mail_root / "inventory.json"
    inside.write_text("[]")
    link = tmp_path / "json_link"
    try:
        link.symlink_to(inside)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(SafetyError):
        assert_safe_write_paths(mail_root, link)

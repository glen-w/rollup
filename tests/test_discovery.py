"""Tests for mbox discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.discovery import build_inventory, filter_folders, iter_mbox_files, list_flat_mbox_names

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"


def test_iter_mbox_files_includes_extensionless() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    names = {f.folder_name for f in folders}
    for expected in ("brainfood", "enviro", "hoops", "misc", "tech"):
        assert expected in names


def test_iter_mbox_ignores_msf() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    for f in folders:
        assert not str(f.mbox_path).endswith(".msf")


def test_nested_sbd_traversal() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    classify_folders = [f for f in folders if f.folder_name.startswith("classify/")]
    assert len(classify_folders) >= 4


def test_folders_sorted_alphabetically() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    names = [f.folder_name for f in folders]
    assert names == sorted(names, key=str.lower)


def test_filter_folders_include() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    filtered = filter_folders(folders, ("tech",), ())
    assert len(filtered) == 1
    assert filtered[0].folder_name == "tech"


def test_filter_folders_exclude() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    filtered = filter_folders(folders, (), ("hoops",))
    assert all(f.folder_name != "hoops" for f in filtered)


def test_build_inventory_counts() -> None:
    import mailbox

    inv = build_inventory(FIXTURE_ROOT)
    tech = next(e for e in inv if e.folder.folder_name == "tech")
    assert tech.message_count == 1
    assert tech.parse_error is None
    mbox = mailbox.mbox(str(tech.folder.mbox_path), create=False)
    assert tech.message_count == len(list(mbox.keys()))
    mbox.close()


def test_dotted_mbox_name_discovered(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    (root / "AI.News").write_bytes(b"From \n")
    (root / "AI.News.msf").write_text("index")
    folders = list(iter_mbox_files(root))
    names = {f.folder_name for f in folders}
    assert "AI.News" in names
    assert all(not str(f.mbox_path).endswith(".msf") for f in folders)


def test_directory_symlink_rejected(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    outside = tmp_path / "outside.sbd"
    outside.mkdir()
    (outside / "leak").write_bytes(b"From \n")
    link = root / "evil.sbd"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported")
    folders = list(iter_mbox_files(root))
    assert all(f.folder_name != "evil/leak" for f in folders)
    assert all("outside" not in str(f.mbox_path.resolve()) for f in folders)


def test_hidden_and_backup_excluded(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    (root / "keep").write_bytes(b"From \n")
    (root / ".hidden").write_bytes(b"From \n")
    (root / "keep.bak").write_bytes(b"From \n")
    names = {f.folder_name for f in iter_mbox_files(root)}
    assert names == {"keep"}


def test_list_flat_mbox_names_skips_sidecars(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    (root / "tech").write_bytes(b"From \n")
    (root / "tech.msf").write_text("index")
    (root / "tech.dat").write_text("data")
    (root / "tech.toc").write_text("toc")
    (root / ".hidden").write_bytes(b"From \n")
    assert list_flat_mbox_names(root) == ["tech"]


def test_list_flat_mbox_names_skips_sbd_dirs(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    (root / "top").write_bytes(b"From \n")
    (root / "nested.sbd").mkdir()
    assert list_flat_mbox_names(root) == ["top"]


def test_list_flat_mbox_names_include_exclude_case_insensitive(tmp_path: Path) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    (root / "Tech").write_bytes(b"From \n")
    (root / "hoops").write_bytes(b"From \n")
    (root / "misc").write_bytes(b"From \n")
    assert list_flat_mbox_names(root, include=("tech",)) == ["Tech"]
    assert list_flat_mbox_names(root, exclude=("HOOPS",)) == ["Tech", "misc"]


def test_list_flat_mbox_names_missing_root() -> None:
    assert list_flat_mbox_names(None) == []
    assert list_flat_mbox_names(Path("/nonexistent/path")) == []

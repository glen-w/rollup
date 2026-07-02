"""Tests for mbox discovery."""

from __future__ import annotations

from pathlib import Path


from rollup.discovery import build_inventory, filter_folders, iter_mbox_files

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

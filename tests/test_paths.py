"""Tests for Thunderbird path discovery."""

from __future__ import annotations

from pathlib import Path

from rollup.paths import discover_newsletters_sbd, resolve_mail_paths


def test_discover_newsletters_sbd(tmp_path: Path) -> None:
    profile = tmp_path / "Library" / "Thunderbird" / "Profiles" / "abc.default"
    account = profile / "Mail" / "Local Folders"
    news = account / "Newsletters.sbd"
    news.mkdir(parents=True)
    found = discover_newsletters_sbd(home=tmp_path)
    assert found == [news.resolve()]


def test_resolve_uses_default_when_present(tmp_path: Path) -> None:
    mail = tmp_path / "email" / "gmail"
    root = mail / "Newsletters.sbd"
    root.mkdir(parents=True)
    resolved = resolve_mail_paths(
        root=root,
        mail_root=mail,
        root_explicit=False,
        mail_root_explicit=False,
        home=tmp_path,
        default_mail_root=mail,
        default_newsletter_root=root,
    )
    assert resolved.source == "default"
    assert resolved.root == root


def test_resolve_discovers_single_candidate(tmp_path: Path) -> None:
    default_mail = tmp_path / "missing-mail"
    default_root = default_mail / "Newsletters.sbd"
    profile = tmp_path / "Library" / "Thunderbird" / "Profiles" / "p1"
    discovered = profile / "Mail" / "Imap" / "Newsletters.sbd"
    discovered.mkdir(parents=True)

    resolved = resolve_mail_paths(
        root=default_root,
        mail_root=default_mail,
        root_explicit=False,
        mail_root_explicit=False,
        home=tmp_path,
        default_mail_root=default_mail,
        default_newsletter_root=default_root,
    )
    assert resolved.source == "discovered"
    assert resolved.root == discovered.resolve()
    assert resolved.mail_root == discovered.parent.resolve()


def test_resolve_multiple_candidates_leaves_default(tmp_path: Path) -> None:
    default_mail = tmp_path / "missing-mail"
    default_root = default_mail / "Newsletters.sbd"
    for name in ("p1", "p2"):
        news = (
            tmp_path
            / "Library"
            / "Thunderbird"
            / "Profiles"
            / name
            / "Mail"
            / "acct"
            / "Newsletters.sbd"
        )
        news.mkdir(parents=True)

    resolved = resolve_mail_paths(
        root=default_root,
        mail_root=default_mail,
        root_explicit=False,
        mail_root_explicit=False,
        home=tmp_path,
        default_mail_root=default_mail,
        default_newsletter_root=default_root,
    )
    assert resolved.source == "default"
    assert len(resolved.candidates) == 2
    assert "Multiple" in resolved.message


def test_explicit_skips_discovery(tmp_path: Path) -> None:
    explicit_root = tmp_path / "custom.sbd"
    explicit_root.mkdir()
    resolved = resolve_mail_paths(
        root=explicit_root,
        mail_root=tmp_path,
        root_explicit=True,
        mail_root_explicit=True,
        home=tmp_path,
        default_mail_root=tmp_path / "nope",
        default_newsletter_root=tmp_path / "nope" / "Newsletters.sbd",
    )
    assert resolved.source == "explicit"
    assert resolved.root == explicit_root

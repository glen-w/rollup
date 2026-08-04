"""Tests for deterministic folder themes."""

from __future__ import annotations

from rollup.folder_theme import (
    FolderThemeOverride,
    accent_for_slug,
    folder_accent_css,
    folder_display_name,
    folder_slug,
    theme_for,
)


def test_folder_slug_normalizes() -> None:
    assert folder_slug("Tech") == "tech"
    assert folder_slug("My Folder") == "my-folder"
    assert folder_slug("  ") == "folder"


def test_accent_stable_across_calls() -> None:
    assert accent_for_slug("tech") == accent_for_slug("tech")
    assert accent_for_slug("tech") != accent_for_slug("hoops")


def test_theme_default_has_no_emoji() -> None:
    theme = theme_for("tech")
    assert theme.emoji is None
    assert theme.accent.startswith("#")
    assert folder_display_name("tech") == "tech"


def test_theme_override_emoji_and_accent() -> None:
    overrides = {
        "tech": FolderThemeOverride(emoji="💻", accent="#4a7fd4"),
    }
    theme = theme_for("tech", overrides)
    assert theme.emoji == "💻"
    assert theme.accent == "#4a7fd4"
    assert folder_display_name("tech", overrides) == "💻 tech"


def test_folder_accent_css_only_includes_present_folders() -> None:
    css = folder_accent_css(["tech"])
    assert ".folder-accent-tech>" in css
    assert ".folder-accent-hoops>" not in css
    assert ".folder-accent-default>" in css

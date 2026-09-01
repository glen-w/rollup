"""Tests for ~/.config/rollup/env loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.env_file import load_rollup_env, parse_env_file_text
from rollup.linkedin.session import normalize_jsession_id


def test_normalize_jsession_id_strips_devtools_copy_artifacts() -> None:
    assert normalize_jsession_id(':"ajax:6587342163708493960"') == "ajax:6587342163708493960"
    assert normalize_jsession_id('"ajax:1"') == "ajax:1"


def test_parse_env_file_text_skips_comments_and_blank_lines() -> None:
    text = """
# LinkedIn session (refresh both together)
export ROLLUP_LINKEDIN_LI_AT=AQEabc123
ROLLUP_LINKEDIN_JSESSIONID="ajax:123"
"""
    assert parse_env_file_text(text) == {
        "ROLLUP_LINKEDIN_LI_AT": "AQEabc123",
        "ROLLUP_LINKEDIN_JSESSIONID": "ajax:123",
    }


def test_load_rollup_env_does_not_override_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "env"
    env_path.write_text(
        "ROLLUP_LINKEDIN_LI_AT=from_file\nROLLUP_LINKEDIN_JSESSIONID=ajax:1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ROLLUP_LINKEDIN_LI_AT", "from_shell")
    monkeypatch.delenv("ROLLUP_LINKEDIN_JSESSIONID", raising=False)

    loaded = load_rollup_env(paths=(env_path,))

    assert loaded == env_path
    import os

    assert os.environ["ROLLUP_LINKEDIN_LI_AT"] == "from_shell"
    assert os.environ["ROLLUP_LINKEDIN_JSESSIONID"] == "ajax:1"


def test_load_rollup_env_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_rollup_env(paths=(tmp_path / "missing",)) is None

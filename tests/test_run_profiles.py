"""Tests for named run profiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rollup.cli import main
from rollup.run_profiles import (
    UnknownRunProfileError,
    list_run_profiles,
    resolve_run_profile,
)


def test_builtin_weekly_and_daily() -> None:
    weekly = resolve_run_profile("weekly")
    daily = resolve_run_profile("daily")
    assert weekly.values["lookback_days"] == 7
    assert daily.values["lookback_days"] == 1
    assert weekly.values.get("no_grouping") is False


def test_toml_custom_profile() -> None:
    profile = resolve_run_profile(
        "sports",
        toml_profiles={"sports": {"lookback_days": 2, "folder": ["hoops"]}},
    )
    assert profile.values["lookback_days"] == 2
    assert profile.values["folder"] == ["hoops"]


def test_toml_overlay_on_builtin() -> None:
    profile = resolve_run_profile(
        "weekly",
        toml_profiles={"weekly": {"effort": "light"}},
    )
    assert profile.values["lookback_days"] == 7
    assert profile.values["effort"] == "light"


def test_unknown_profile_raises() -> None:
    with pytest.raises(UnknownRunProfileError):
        resolve_run_profile("nope")


def test_list_includes_custom() -> None:
    names = {
        p.name
        for p in list_run_profiles(toml_profiles={"sports": {"lookback_days": 2}})
    }
    assert "weekly" in names
    assert "daily" in names
    assert "sports" in names


def test_cli_config_print_daily(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "config",
                "print",
                "--root",
                str(root),
                "--mail-root",
                str(tmp_path),
                "--profile",
                "daily",
            ]
        )
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_profile"] == "daily"
    assert payload["lookback_days"] == 1


def test_cli_lookback_beats_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "config",
                "print",
                "--root",
                str(root),
                "--mail-root",
                str(tmp_path),
                "--profile",
                "daily",
                "--lookback-days",
                "4",
            ]
        )
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lookback_days"] == 4


def test_cli_toml_config_and_effort_compose(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    cfg = tmp_path / "rollup.toml"
    cfg.write_text(
        'effort = "high"\n[profiles.weekly]\nlookback_days = 5\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                str(cfg),
                "config",
                "print",
                "--root",
                str(root),
                "--mail-root",
                str(tmp_path),
                "--profile",
                "weekly",
                "--effort",
                "light",
            ]
        )
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lookback_days"] == 5
    assert payload["effort"] == "light"

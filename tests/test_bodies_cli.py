"""CLI tests for rollup bodies maintenance commands."""

from __future__ import annotations

import json
from pathlib import Path

from rollup.bodies_cmd import cmd_bodies
from rollup.cli import build_parser
from rollup.state import init_db


def test_bodies_stats_json(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    parser = build_parser()
    args = parser.parse_args(
        ["bodies", "stats", "--state-dir", str(state), "--json"]
    )
    assert cmd_bodies(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_rows"] == 0
    assert "orphans" in data
    assert "coverage_pct" in data


def test_bodies_check_json(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    parser = build_parser()
    args = parser.parse_args(
        ["bodies", "check", "--state-dir", str(state), "--json"]
    )
    assert cmd_bodies(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert "schema_version" in data
    assert isinstance(data["issues"], list)


def test_bodies_prune_requires_yes_or_dry_run(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    parser = build_parser()
    args = parser.parse_args(["bodies", "prune", "--state-dir", str(state)])
    assert cmd_bodies(args) == 1


def test_bodies_delete_requires_yes_or_dry_run(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    parser = build_parser()
    args = parser.parse_args(["bodies", "delete", "--state-dir", str(state)])
    assert cmd_bodies(args) == 1


def test_bodies_vacuum_requires_yes(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    parser = build_parser()
    args = parser.parse_args(["bodies", "vacuum", "--state-dir", str(state)])
    assert cmd_bodies(args) == 1


def test_bodies_prune_dry_run(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    parser = build_parser()
    args = parser.parse_args(
        ["bodies", "prune", "--state-dir", str(state), "--dry-run"]
    )
    assert cmd_bodies(args) == 0
    assert "orphans:" in capsys.readouterr().out

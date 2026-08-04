"""Ensure ordinary CLI paths do not hard-require Flask."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "rollup"


def test_core_modules_do_not_import_flask():
    for name in ("pipeline.py", "doctor.py", "sources_cmd.py", "cli.py", "run_index.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "import flask" not in text
        assert "from flask" not in text


def test_web_cli_lazy_loads_flask():
    text = (ROOT / "web" / "cli_web.py").read_text(encoding="utf-8")
    assert "import flask" in text or "from flask" in text or "import flask" in text.lower()
    # Top-level of cli_web should not import flask at module import for create_app path
    # cmd_web calls _ensure_flask before importing app
    assert "def _ensure_flask" in text


def test_cmd_web_reindex_zero(tmp_path: Path) -> None:
    from argparse import Namespace

    from rollup.web.cli_web import cmd_web_reindex

    state = tmp_path / "state"
    out = tmp_path / "out"
    mail = tmp_path / "mail"
    state.mkdir()
    out.mkdir()
    mail.mkdir()
    args = Namespace(
        state_dir=str(state),
        output_dir=str(out),
        mail_root=str(mail),
    )
    assert cmd_web_reindex(args) == 0


def test_cmd_web_missing_flask(monkeypatch, capsys) -> None:
    from argparse import Namespace

    from rollup.web import cli_web

    monkeypatch.setattr(cli_web, "_ensure_flask", lambda: "Flask is required")
    args = Namespace(web_command=None)
    assert cli_web.cmd_web(args) == 1
    assert "Flask is required" in capsys.readouterr().err

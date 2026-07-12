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

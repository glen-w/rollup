"""Golden-output guard for the Markdown and HTML renderers.

Renders the fixture store with a fixed clock, no LLM, grouping on, and every
briefing layer (attention, coverage, glance) at its default. The outputs are
compared byte-for-byte against ``tests/fixtures/golden/``.

Regenerate deliberately with::

    ROLLUP_UPDATE_GOLDENS=1 python -m pytest tests/test_render_golden.py

and review the diff. A golden change is a product change, not a test fix.
"""

from __future__ import annotations

import difflib
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.clock import FixedClock
from rollup.config import Config
from rollup.pipeline import run_digest
from rollup.run_options import GroupingConfig, RunOptions

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
NOW = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
UPDATE = os.environ.get("ROLLUP_UPDATE_GOLDENS") == "1"


def _config(tmp_path: Path) -> Config:
    return Config(
        root=FIXTURE_ROOT,
        mail_root=FIXTURE_ROOT.parent,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=3650,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=200_000,
        max_chars_for_llm=30_000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="llama3.2:3b",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    tmp_path = tmp_path_factory.mktemp("golden")
    config = _config(tmp_path)
    config.state_dir.mkdir(parents=True)
    result = run_digest(
        config,
        RunOptions(dry_run=False, write_manifest=False),
        grouping=GroupingConfig(enabled=True),
        clock=FixedClock(NOW),
        acquire_lock=False,
    )
    assert result.status == "success", result.error_message
    assert result.md_path is not None and result.html_path is not None
    return {
        "digest.md": result.md_path.read_text(encoding="utf-8"),
        "digest.html": result.html_path.read_text(encoding="utf-8"),
    }


def _assert_golden(name: str, actual: str) -> None:
    path = GOLDEN_DIR / name
    if UPDATE:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    assert path.is_file(), f"missing golden {path}; run with ROLLUP_UPDATE_GOLDENS=1"
    expected = path.read_text(encoding="utf-8")
    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile=f"golden/{name}",
                tofile=f"actual/{name}",
                lineterm="",
                n=2,
            )
        )
        pytest.fail(f"render output drifted from golden {name}:\n{diff[:6000]}")


@pytest.mark.parametrize("name", ["digest.md", "digest.html"])
def test_render_matches_golden(rendered: dict[str, str], name: str) -> None:
    _assert_golden(name, rendered[name])


def test_golden_is_not_trivial(rendered: dict[str, str]) -> None:
    md = rendered["digest.md"]
    html = rendered["digest.html"]
    assert "## Contents" in md
    assert "class='rollup-toc'" in html
    # Every fixture folder is a section in both outputs.
    for folder in ("brainfood", "enviro", "hoops", "misc", "tech", "trackerwall"):
        assert f"## {folder}" in md, folder
        assert f"id='folder-{folder}'" in html or f"folder-{folder}" in html, folder

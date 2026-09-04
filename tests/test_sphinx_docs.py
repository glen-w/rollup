"""Sphinx hosted docs stay a view over the in-repo Markdown corpus."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

TOCTREE_RE = re.compile(r"```\{toctree\}(.*?)```", re.S)


def _toctree_entries() -> set[str]:
    text = (DOCS / "index.md").read_text(encoding="utf-8")
    found: set[str] = set()
    for block in TOCTREE_RE.findall(text):
        glob = ":glob:" in block
        entries = [
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.strip().startswith(":")
        ]
        for entry in entries:
            if glob and "*" in entry:
                for path in DOCS.glob(entry):
                    if path.suffix == ".md" and path.is_file():
                        found.add(path.relative_to(DOCS).as_posix())
            else:
                rel = entry if entry.endswith(".md") else f"{entry}.md"
                found.add(rel)
    return found


def test_sphinx_kit_files_exist():
    for rel in (
        "docs/conf.py",
        "scripts/release/build_docs.sh",
        "scripts/release/assemble_pages_site.sh",
        ".readthedocs.yml",
        ".github/workflows/pages.yml",
        ".github/workflows/docs.yml",
        "docs/_static/.gitkeep",
        "docs/_templates/page.html",
        "website/index.html",
        "website/chrome/site_chrome.css",
        "website/chrome/site_nav.js",
        "website/images/rollup_logo.png",
    ):
        assert (ROOT / rel).is_file(), f"missing {rel}"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "scripts/release/build_docs.sh" in makefile
    assert "pages-site" in makefile
    assert "assemble_pages_site.sh" in makefile
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^docs\s*=\s*\[", pyproject, re.M)
    assert "myst-parser" in pyproject
    assert "furo" in pyproject
    conf = (DOCS / "conf.py").read_text(encoding="utf-8")
    assert "myst_parser" in conf
    assert "furo" in conf
    assert "../website/chrome" in conf
    assert "site_chrome.css" in conf
    landing = (ROOT / "website" / "index.html").read_text(encoding="utf-8")
    assert "Rollup" in landing
    assert "./guide/" in landing
    assert "127.0.0.1:8765" in landing


def test_live_markdown_is_in_hosted_toctree():
    """New live docs must appear in docs/index.md (entry or glob) — no second corpus."""
    hosted = _toctree_entries()
    skip = {"index.md"}
    missing: list[str] = []
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS).as_posix()
        if rel in skip:
            continue
        if rel not in hosted:
            missing.append(rel)
    assert missing == [], (
        "live docs missing from docs/index.md toctree "
        f"(add a glob or explicit entry): {missing}"
    )


def test_docs_workflow_builds_sphinx():
    text = (ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    assert "pip install -e \".[docs]\"" in text or "pip install -e '.[docs]'" in text
    assert "make docs" in text
    assert "docs-html" in text
    pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "assemble_pages_site.sh" in pages


def test_gitignore_excludes_sphinx_and_pages_output():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "docs/_build/" in gitignore
    assert "_site/" in gitignore


def test_assemble_script_skips_website_readme():
    script = (ROOT / "scripts" / "release" / "assemble_pages_site.sh").read_text(
        encoding="utf-8"
    )
    assert 'README.md' in script
    assert "DOCS_BUILD_DIR=" in script
    assert "_site/guide" in script or '${OUT}/guide' in script
    assert (ROOT / "website" / "README.md").is_file()


def test_makefile_is_docs_only():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-smoke" not in makefile
    assert "test-fast" not in makefile
    assert "pages-site:" in makefile
    assert "docs-clean:" in makefile


def test_landing_chrome_and_version_match_package():
    landing = (ROOT / "website" / "index.html").read_text(encoding="utf-8")
    chrome = (ROOT / "website" / "chrome" / "site_chrome.css").read_text(
        encoding="utf-8"
    )
    template = (ROOT / "docs" / "_templates" / "page.html").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert version, "pyproject.toml version missing"
    assert f"Version {version.group(1)}" in landing
    assert "ru-site-chrome" in landing
    assert "ru-site-chrome" in chrome
    assert "ru-site-chrome" in template
    assert "content_root ~ '../index.html'" in template
    assert "glen-w/rollup" in landing
    rtd = (ROOT / ".readthedocs.yml").read_text(encoding="utf-8")
    assert "docs/conf.py" in rtd
    assert "extra_requirements:" in rtd

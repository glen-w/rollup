# Sphinx configuration for Rollup hosted docs.
# Build: make docs | sphinx-build -b html docs docs/_build/html
# Corpus: the same Markdown under docs/ (no second tree).

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _release() -> str:
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version("rollup")
    except Exception:
        init = (ROOT / "src" / "rollup" / "__init__.py").read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
        return match.group(1) if match else "dev"


project = "Rollup"
author = "Rollup contributors"
copyright = f"{date.today().year}, {author}"

release = _release()
version = ".".join(str(release).split(".")[:2])

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
    "sphinxcontrib.mermaid",
]

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

master_doc = "index"

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "replacements",
    "smartquotes",
    "strikethrough",
    "tasklist",
]
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]
# GitHub-style relative links must not fail the build.
suppress_warnings = ["myst.xref_missing", "misc.highlighting_failure"]

html_theme = "furo"
html_title = "Rollup"
# Shared public-site chrome (header nav) lives under website/chrome/ so the
# marketing landing and /guide/ share one sticky header.
html_static_path = ["_static", "../website/chrome"]
html_css_files = ["site_chrome.css"]
html_js_files = ["site_nav.js"]

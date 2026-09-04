#!/usr/bin/env bash
# Maintainer: build Sphinx HTML docs into docs/_build/html from the in-repo Markdown.
# Usage (from repo root): bash scripts/release/build_docs.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ ! -f docs/conf.py ]]; then
  echo "error: docs/conf.py missing — Sphinx tree not present" >&2
  exit 1
fi

if [[ -x "$ROOT/.venv/bin/sphinx-build" ]]; then
  SPHINX_BUILD="$ROOT/.venv/bin/sphinx-build"
elif command -v sphinx-build >/dev/null 2>&1; then
  SPHINX_BUILD="$(command -v sphinx-build)"
else
  echo "error: sphinx-build not found. Install with: pip install -e '.[docs]'" >&2
  exit 1
fi

OUT="${DOCS_BUILD_DIR:-docs/_build/html}"
mkdir -p "$(dirname "$OUT")"
echo "Building Sphinx HTML → ${OUT}"
# Warnings are allowed until a public hosted hostname is live.
"$SPHINX_BUILD" -b html docs "$OUT"
echo "OK: open ${OUT}/index.html"

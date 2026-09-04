#!/usr/bin/env bash
# Assemble the GitHub Pages payload: marketing site + Sphinx HTML guide.
# Source of truth for guide content is docs/*.md (same tree Sphinx/RTD builds).
#
# Usage (from repo root):
#   bash scripts/release/assemble_pages_site.sh
#   PAGES_OUT=_site bash scripts/release/assemble_pages_site.sh
#
# Requires: pip install -e '.[docs]' (sphinx-build on PATH)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

OUT="${PAGES_OUT:-_site}"
GUIDE_DIR="${OUT}/guide"

if [[ ! -d website ]]; then
  echo "error: website/ missing" >&2
  exit 1
fi
if [[ ! -f docs/conf.py ]]; then
  echo "error: docs/conf.py missing" >&2
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"

# Marketing landing at site root (exclude maintainer README from public root).
shopt -s dotglob nullglob
for item in website/*; do
  base="$(basename "$item")"
  if [[ "$base" == "README.md" ]]; then
    continue
  fi
  cp -a "$item" "$OUT/"
done
shopt -u dotglob nullglob

echo "Building Sphinx HTML → ${GUIDE_DIR}"
DOCS_BUILD_DIR="$GUIDE_DIR" bash scripts/release/build_docs.sh

# Drop Sphinx inventory / cache noise from the published tree if present.
rm -rf "${GUIDE_DIR}/.doctrees" "${GUIDE_DIR}/.buildinfo" 2>/dev/null || true

echo "OK: Pages site assembled at ${OUT}/ (guide at ${GUIDE_DIR}/)"

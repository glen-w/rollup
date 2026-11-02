# Rollup maintainer docs targets.

.DEFAULT_GOAL := help

.PHONY: help check lint types test fix docs docs-clean pages-site

help:
	@echo "Rollup Makefile"
	@echo ""
	@echo "Quality gate (same commands CI runs):"
	@echo "  check             ruff + mypy + pytest with coverage gate"
	@echo "  lint              ruff check src tests"
	@echo "  types             mypy (config in pyproject.toml)"
	@echo "  test              pytest with --cov-fail-under=80"
	@echo "  fix               ruff --fix (safe fixes only)"
	@echo ""
	@echo "Docs:"
	@echo "  docs              Build Sphinx HTML into docs/_build/html (requires .[docs])"
	@echo "  docs-clean        Remove Sphinx build artifacts"
	@echo "  pages-site        Assemble website/ + Sphinx guide into _site/ (GitHub Pages)"
	@echo ""
	@echo "Usage: pip install -e '.[docs]' && make docs"
	@echo "       make pages-site"

check: lint types test

lint:
	ruff check src tests

types:
	mypy

test:
	python -m pytest tests/ -q --cov=rollup --cov-report=term --cov-fail-under=80

fix:
	ruff check src tests --fix

docs:
	@bash scripts/release/build_docs.sh

docs-clean:
	@echo "Cleaning Sphinx build artifacts..."
	@rm -rf docs/_build _site
	@echo "Documentation build cleaned."

pages-site:
	@bash scripts/release/assemble_pages_site.sh

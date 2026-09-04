# Rollup maintainer docs targets.

.DEFAULT_GOAL := help

.PHONY: help docs docs-clean pages-site

help:
	@echo "Rollup Makefile"
	@echo ""
	@echo "Docs:"
	@echo "  docs              Build Sphinx HTML into docs/_build/html (requires .[docs])"
	@echo "  docs-clean        Remove Sphinx build artifacts"
	@echo "  pages-site        Assemble website/ + Sphinx guide into _site/ (GitHub Pages)"
	@echo ""
	@echo "Usage: pip install -e '.[docs]' && make docs"
	@echo "       make pages-site"

docs:
	@bash scripts/release/build_docs.sh

docs-clean:
	@echo "Cleaning Sphinx build artifacts..."
	@rm -rf docs/_build _site
	@echo "Documentation build cleaned."

pages-site:
	@bash scripts/release/assemble_pages_site.sh

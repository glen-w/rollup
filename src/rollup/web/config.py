"""Shared web helpers for loading the active TOML config document."""

from __future__ import annotations

from flask import current_app

from rollup.config_service import ConfigDocument, load_document


def load_web_config_document() -> ConfigDocument:
    """Load TOML using Flask ``CONFIG_PATH`` / ``CONFIG_EXPLICIT`` app config."""
    config_path = current_app.config.get("CONFIG_PATH")
    if current_app.config.get("CONFIG_EXPLICIT") and config_path:
        return load_document(explicit=config_path)
    if config_path:
        return load_document(explicit=config_path)
    return load_document()

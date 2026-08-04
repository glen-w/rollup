"""Bundled branding assets for rollup output."""

from __future__ import annotations

from importlib import resources

LOGO_FILENAME = "rollup_logo.png"
EINK_LOGO_FILENAME = "rollup_logo_eink.png"
FAVICON_FILENAME = "favicon.ico"


def asset_bytes(name: str) -> bytes:
    return resources.files(__package__).joinpath(name).read_bytes()


def digest_logo_bytes() -> bytes:
    """Small grayscale logo for digest output dirs and EPUB (e-ink friendly)."""
    return asset_bytes(EINK_LOGO_FILENAME)

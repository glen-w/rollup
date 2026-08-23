"""Bundled branding assets for rollup output."""

from __future__ import annotations

from importlib import resources

LOGO_FILENAME = "rollup_logo.png"
EINK_LOGO_FILENAME = "rollup_logo_eink.png"
EPUB_COVER_FILENAME = "rollup_cover_epub.png"
FAVICON_FILENAME = "favicon.ico"


def asset_bytes(name: str) -> bytes:
    return resources.files(__package__).joinpath(name).read_bytes()


def digest_logo_bytes() -> bytes:
    """Small grayscale logo for digest output dirs (e-ink friendly)."""
    return asset_bytes(EINK_LOGO_FILENAME)


def epub_cover_bytes() -> bytes:
    """Larger light greyscale portrait cover for EPUB (library / screensaver)."""
    return asset_bytes(EPUB_COVER_FILENAME)

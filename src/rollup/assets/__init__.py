"""Bundled branding assets for rollup output."""

from __future__ import annotations

from importlib import resources

LOGO_FILENAME = "rollup_logo.png"
FAVICON_FILENAME = "favicon.ico"


def asset_bytes(name: str) -> bytes:
    return resources.files(__package__).joinpath(name).read_bytes()

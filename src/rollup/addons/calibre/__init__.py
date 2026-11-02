"""Calibre drop-folder destination (config is import-safe; writer is lazy)."""

from __future__ import annotations

from rollup.addons.calibre.config import CalibreConfig, parse_calibre_config

__all__ = ["CalibreConfig", "CalibreOutputWriter", "parse_calibre_config"]


def __getattr__(name: str):
    if name == "CalibreOutputWriter":
        from rollup.addons.calibre.writer import CalibreOutputWriter

        return CalibreOutputWriter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Send-to-Kindle optional destination writer."""

from __future__ import annotations

from typing import Any

__all__ = ["KindleConfig", "KindleOutputWriter", "parse_kindle_config"]


def __getattr__(name: str) -> Any:
    if name == "KindleOutputWriter":
        from rollup.addons.kindle.writer import KindleOutputWriter

        return KindleOutputWriter
    if name == "KindleConfig":
        from rollup.addons.kindle.config import KindleConfig

        return KindleConfig
    if name == "parse_kindle_config":
        from rollup.addons.kindle.config import parse_kindle_config

        return parse_kindle_config
    raise AttributeError(name)

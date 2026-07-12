"""Local Flask web UI package. Must not be imported by digest/doctor/sources paths."""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    from rollup.web.app import create_app as _create_app

    return _create_app(*args, **kwargs)

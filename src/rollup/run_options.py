"""Invocation/runtime options separate from domain Config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class RunOptions:
    """Per-invocation runtime behaviour (not domain configuration)."""

    dry_run: bool = False
    cron: bool = False
    quiet: bool = False
    verbose: bool = False
    write_manifest: bool = True
    publish_latest: bool = False
    allow_partial_latest: bool = False
    mode: Literal["manual", "cron"] = "manual"


@dataclass(frozen=True)
class ManifestConfig:
    """Where and how run manifests are stored."""

    manifest_dir: Path
    schema_version: int = 1


@dataclass(frozen=True)
class GroupingConfig:
    """Conservative deterministic grouping settings."""

    enabled: bool = True
    min_group_size: int = 3
    report: bool = False


def resolve_run_options(
    *,
    dry_run: bool = False,
    cron: bool = False,
    quiet: bool | None = None,
    verbose: bool = False,
    write_manifest: bool | None = None,
    publish_latest: bool | None = None,
    allow_partial_latest: bool = False,
    no_manifest: bool = False,
) -> RunOptions:
    """Resolve RunOptions with --cron defaults that do not override explicit flags.

    Precedence: explicit CLI flags > --cron defaults > built-in defaults.
    """
    mode: Literal["manual", "cron"] = "cron" if cron else "manual"

    if quiet is None:
        quiet = True if cron else False
    # Explicit --verbose wins over cron-implied quiet.
    if verbose:
        quiet = False

    if write_manifest is None:
        write_manifest = not dry_run and not no_manifest
    if no_manifest:
        write_manifest = False

    if publish_latest is None:
        publish_latest = bool(cron) and not dry_run

    return RunOptions(
        dry_run=dry_run,
        cron=cron,
        quiet=quiet,
        verbose=verbose,
        write_manifest=write_manifest,
        publish_latest=publish_latest,
        allow_partial_latest=allow_partial_latest,
        mode=mode,
    )


def default_manifest_config(state_dir: Path) -> ManifestConfig:
    return ManifestConfig(manifest_dir=state_dir / "manifests")

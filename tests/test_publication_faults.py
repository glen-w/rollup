"""Fault injection across the publication boundary.

CONTRACT.md orders publication as: stage → fsync + rename dated artifacts →
required writers → (irreversible) latest.* → seen-state → manifest → web index.
Each test below breaks exactly one step and asserts the exit code, that
``latest.*`` and seen-state only advance when they are allowed to, that no
half-published dated pair survives, and that the manifest records the
degradation. The helpers come from ``test_hardening_contracts``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import pytest

from rollup.clock import FixedClock
from rollup.pipeline import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS, run_digest
from rollup.render import cleanup_stale_temps
from rollup.run_options import GroupingConfig, RunOptions
from test_hardening_contracts import (
    NOW,
    _config,
    _digest_files,
    _run,
    _seen_count,
)

OLD = "generation=old\n"


def _seed_latest(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest.md").write_text(OLD, encoding="utf-8")
    (output_dir / "latest.html").write_text(OLD, encoding="utf-8")


def _latest_is_old(output_dir: Path) -> bool:
    md = output_dir / "latest.md"
    html = output_dir / "latest.html"
    return (
        md.exists()
        and html.exists()
        and md.read_text(encoding="utf-8") == OLD
        and html.read_text(encoding="utf-8") == OLD
    )


def _manifest(config) -> dict:
    manifests = [
        p
        for p in (config.state_dir / "manifests").glob("*.json")
        if p.name != "latest.json"
    ]
    assert len(manifests) == 1, manifests
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def _event_codes(manifest: dict) -> set[str]:
    return {e["code"] for e in manifest.get("lifecycle_events", [])}


class _Writer:
    """Minimal OutputWriter that always fails."""

    def __init__(self, name: str, *, required: bool) -> None:
        self.name = name
        self.required = required

    def register_cli(self, parser: argparse.ArgumentParser) -> None:  # pragma: no cover
        pass

    def enabled(self, args: argparse.Namespace, config) -> bool:
        return True

    def write(self, report, ctx) -> list[Path]:
        raise RuntimeError(f"{self.name}: disk full")


def _run_with_writers(config, writers: dict, *, publish_latest: bool = True):
    return run_digest(
        config,
        RunOptions(publish_latest=publish_latest, write_manifest=True),
        grouping=GroupingConfig(enabled=False),
        manifest_config=None,
        clock=FixedClock(NOW),
        output_writers=writers,
        writer_cli_args=argparse.Namespace(output=None),
    )


# --- step: dated artifact staging -------------------------------------------


def test_dated_write_fsyncs_before_rename(tmp_path: Path, monkeypatch) -> None:
    """Staged digest files are fsynced before the rename into their final names."""
    config = _config(tmp_path)
    synced: list[str] = []
    real_fsync = os.fsync
    real_rename = Path.rename

    def record_fsync(fd: int) -> None:
        # Resolve the fd back to a path where the platform allows it.
        try:
            synced.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            synced.append(f"fd:{fd}")
        real_fsync(fd)

    renamed_after_sync: list[bool] = []

    def record_rename(self: Path, target: Path):
        if self.name.startswith(".tmp-") and target.suffix in (".md", ".html"):
            renamed_after_sync.append(bool(synced))
        return real_rename(self, target)

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(Path, "rename", record_rename)

    result = _run(config, run_options=RunOptions(write_manifest=False))
    assert result.exit_code == EXIT_SUCCESS
    assert renamed_after_sync and all(renamed_after_sync)
    # md + html at minimum.
    assert len(synced) >= 2


def test_crash_between_dated_renames_leaves_no_half_pair(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_latest(config.output_dir)
    real_rename = Path.rename

    def fail_html(self: Path, target: Path):
        if target.suffix == ".html" and self.name.startswith(".tmp-"):
            raise OSError("power loss between renames")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_html)

    result = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))

    assert result.exit_code == EXIT_FAILURE
    assert not _digest_files(config.output_dir, "md")
    assert not _digest_files(config.output_dir, "html")
    assert not list(config.output_dir.glob(".tmp-*"))
    assert _latest_is_old(config.output_dir)
    assert _seen_count(config) == 0
    assert _manifest(config)["status"] == "failure"


def test_stale_temps_from_prior_crash_are_cleaned(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.output_dir.mkdir(parents=True)
    stale = config.output_dir / ".tmp-2026-01-01T000000Z-deadbeef-newsletter-digest.md"
    stale.write_text("half-written", encoding="utf-8")

    result = _run(config, run_options=RunOptions(write_manifest=False))

    assert result.exit_code == EXIT_SUCCESS
    assert not stale.exists()
    assert not list(config.output_dir.glob(".tmp-*"))


def test_cleanup_stale_temps_tolerates_unlink_errors(tmp_path: Path, monkeypatch) -> None:
    stale = tmp_path / ".tmp-x.md"
    stale.write_text("x", encoding="utf-8")

    def boom(self: Path, *a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", boom)
    cleanup_stale_temps(tmp_path)  # must not raise


# --- step: required / optional writers ---------------------------------------


def test_required_writer_failure_is_hard_failure_and_blocks_boundary(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _seed_latest(config.output_dir)

    result = _run_with_writers(config, {"boom": _Writer("boom", required=True)})

    assert result.exit_code == EXIT_FAILURE
    assert result.aggregated.required_writer_failed is True
    # Dated core artifacts had already landed; they are kept for inspection…
    assert _digest_files(config.output_dir, "md")
    # …but nothing crossed the irreversible boundary.
    assert _latest_is_old(config.output_dir)
    assert result.aggregated.latest_outputs_updated is False
    assert _seen_count(config) == 0
    manifest = _manifest(config)
    assert manifest["status"] == "failure"
    assert "required_writer_failed" in _event_codes(manifest)
    assert "required_writer_failed" in manifest.get("degradations", [])


def test_optional_writer_failure_is_partial_and_keeps_last_good_latest(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _seed_latest(config.output_dir)

    result = _run_with_writers(config, {"opds": _Writer("opds", required=False)})

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.optional_writer_failed is True
    assert _digest_files(config.output_dir, "md")
    # Partial without allow_partial_latest: last-known-good latest stays.
    assert _latest_is_old(config.output_dir)
    # Seen-state does advance: the required digest is published.
    assert _seen_count(config) == 1
    manifest = _manifest(config)
    assert manifest["status"] == "partial"
    assert "optional_writer_failed" in manifest["degradations"]
    assert "optional_writer_failed" in _event_codes(manifest)


def test_optional_failure_after_required_success_keeps_required_artifacts(
    tmp_path: Path,
) -> None:
    """Writer order: a required writer that wrote must not be rolled back by a later optional failure."""
    config = _config(tmp_path)
    written: list[Path] = []

    class _Ok(_Writer):
        def write(self, report, ctx) -> list[Path]:
            p = ctx.output_dir / f"{self.name}.txt"
            p.write_text("ok", encoding="utf-8")
            written.append(p)
            return [p]

    result = _run_with_writers(
        config,
        {"txt": _Ok("txt", required=True), "calibre": _Writer("calibre", required=False)},
    )
    assert result.exit_code == EXIT_PARTIAL
    assert written and all(p.exists() for p in written)
    assert [p.name for p in result.aggregated.writer_artifacts] == ["txt.txt"]


# --- step: archive previous batch --------------------------------------------


def test_archive_failure_is_partial_not_hard_failure(tmp_path: Path, monkeypatch) -> None:
    """Housekeeping after the new batch landed must not fail a usable digest."""
    config = _config(tmp_path)

    monkeypatch.setattr(
        "rollup.output_archive.archive_previous_outputs",
        lambda *a, **k: (_ for _ in ()).throw(OSError("archive dir unwritable")),
    )

    result = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.archive_failed is True
    assert _digest_files(config.output_dir, "md")
    # Required publication succeeded, so seen-state advances.
    assert _seen_count(config) == 1
    manifest = _manifest(config)
    assert manifest["status"] == "partial"
    assert "archive_failed" in manifest["degradations"]
    assert "archive_failed" in _event_codes(manifest)


# --- step: latest.* ----------------------------------------------------------


def test_latest_failure_keeps_old_pair_and_advances_seen(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_latest(config.output_dir)

    def boom(**_kwargs):
        raise OSError("latest publish failed")

    monkeypatch.setattr("rollup.publication.publish_latest_outputs", boom)

    result = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))

    assert result.exit_code == EXIT_PARTIAL
    assert _latest_is_old(config.output_dir)
    assert _seen_count(config) == 1
    manifest = _manifest(config)
    assert "publication_failed" in manifest["degradations"]
    assert "publication_failed" in _event_codes(manifest)


def test_empty_window_refuses_latest_and_records_event(tmp_path: Path) -> None:
    from test_hardening_contracts import _mail_tree

    root = _mail_tree(tmp_path, include_undated=False)
    # Window far from the fixture date → zero included, healthy discovery/parse.
    config = _config(tmp_path, root=root, lookback_days=1)
    _seed_latest(config.output_dir)

    result = run_digest(
        config,
        RunOptions(publish_latest=True, write_manifest=True),
        grouping=GroupingConfig(enabled=False),
        clock=FixedClock(NOW.replace(year=2030)),
    )
    assert result.exit_code == EXIT_SUCCESS
    assert result.aggregated.messages_included == 0
    assert _latest_is_old(config.output_dir)
    assert "latest_skipped_empty_window" in _event_codes(_manifest(config))


# --- step: seen-state ---------------------------------------------------------


def test_seen_failure_is_partial_and_latest_still_published(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr("rollup.state.upsert_seen_keys", boom)
    result = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.seen_state_failed is True
    # latest.* is decided before seen-state; a seen failure cannot retract it.
    assert (config.output_dir / "latest.md").exists()
    assert _seen_count(config) == 0
    assert "seen_state_failed" in _manifest(config)["degradations"]


# --- step: manifest and web index -------------------------------------------


def test_manifest_failure_does_not_touch_dated_or_latest(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def boom(self, *, update_latest: bool = False):
        raise OSError("manifest dir read-only")

    monkeypatch.setattr("rollup.manifest.ManifestBuilder.write_if_state_writable", boom)
    result = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.manifest_write_failed is True
    assert _digest_files(config.output_dir, "md")
    assert (config.output_dir / "latest.md").exists()
    assert _seen_count(config) == 1


def test_web_index_failure_is_secondary_and_exit_zero(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "rollup.run_index.index_rollup_run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("index boom")),
    )
    result = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))

    assert result.exit_code == EXIT_SUCCESS
    assert result.status == "success"
    assert result.aggregated.web_index_failed is True
    assert result.aggregated.web_index_error == "index boom"
    assert (config.output_dir / "latest.md").exists()


# --- recovery on the next run --------------------------------------------------


def test_next_run_after_crash_publishes_cleanly(tmp_path: Path, monkeypatch) -> None:
    """A crash mid-dated-write is fully recovered by the next successful run."""
    config = _config(tmp_path)
    _seed_latest(config.output_dir)
    real_rename = Path.rename
    armed = {"fail": True}

    def flaky(self: Path, target: Path):
        if armed["fail"] and target.suffix == ".html" and self.name.startswith(".tmp-"):
            armed["fail"] = False
            raise OSError("crash once")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)

    first = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))
    assert first.exit_code == EXIT_FAILURE
    assert _seen_count(config) == 0
    assert _latest_is_old(config.output_dir)

    second = _run(config, run_options=RunOptions(publish_latest=True, write_manifest=True))
    assert second.exit_code == EXIT_SUCCESS
    assert len(_digest_files(config.output_dir, "md")) == 1
    assert len(_digest_files(config.output_dir, "html")) == 1
    assert not list(config.output_dir.glob(".tmp-*"))
    assert not _latest_is_old(config.output_dir)
    assert _seen_count(config) == 1


@pytest.mark.parametrize(
    "flag",
    ["archive_failed", "optional_writer_failed", "publication_failed", "seen_state_failed"],
)
def test_partial_reasons_and_manifest_flags_agree(flag: str) -> None:
    """Every partial flag has a CLI label and a manifest degradation entry."""
    from rollup.manifest import _DEGRADATION_ATTRS
    from rollup.pipeline.status import _PARTIAL_REASONS, derive_run_status
    from rollup.pipeline.types import AggregatedResults

    agg = AggregatedResults(usable_digest=True, dated_outputs_written=True)
    setattr(agg, flag, True)
    assert derive_run_status(agg) == "partial"
    assert flag in {attr for attr, _ in _PARTIAL_REASONS}
    assert flag in _DEGRADATION_ATTRS

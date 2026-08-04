"""Tests for output writer discovery and enablement."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from rollup.config import Config
from rollup.models import DigestReport, DigestStats
from rollup.output_writers import (
    OutputWriterError,
    WriteContext,
    builtin_writers,
    discover_writers,
    requested_writer_names,
    run_enabled_writers,
    validate_requested_writers,
)


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"xteink": False, "output": []}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _config(tmp_path: Path | None = None) -> Config:
    root = tmp_path or Path("/tmp")
    return Config(
        root=root,
        mail_root=root / "mail",
        output_dir=root / "out",
        state_dir=root / "state",
        log_dir=root / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=200_000,
        max_chars_for_llm=30_000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="llama3.2:3b",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
    )


def _empty_report() -> DigestReport:
    now = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
        dated_by_folder={},
        undated=(),
        stats=DigestStats(
            folders_scanned=0,
            messages_parsed=0,
            dated_included=0,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=0,
        ),
    )


def test_builtin_discovers_xteink() -> None:
    writers = builtin_writers()
    assert "xteink" in writers
    assert writers["xteink"].name == "xteink"


def test_discover_writers_includes_xteink() -> None:
    writers = discover_writers()
    assert "xteink" in writers


def test_requested_writer_names_default_all() -> None:
    assert requested_writer_names(_args()) is None


def test_requested_writer_names_none() -> None:
    assert requested_writer_names(_args(output=["none"])) == set()


def test_requested_writer_names_xteink_flag() -> None:
    assert requested_writer_names(_args(xteink=True)) == {"xteink"}


def test_requested_writer_names_x3_alias() -> None:
    """Legacy --x3 / --output x3 still map to the xteink writer."""
    assert requested_writer_names(_args(x3=True)) == {"xteink"}
    assert requested_writer_names(_args(output=["x3"])) == {"xteink"}
    assert requested_writer_names(_args(output=["X3"])) == {"xteink"}


def test_requested_writer_names_output_flag() -> None:
    assert requested_writer_names(_args(output=["xteink"])) == {"xteink"}
    assert requested_writer_names(_args(output=["XTEINK", "xteink"])) == {"xteink"}


def test_requested_writer_names_combined() -> None:
    assert requested_writer_names(_args(xteink=True, output=["epub"])) == {"xteink", "epub"}


def test_validate_none_mixed_with_names() -> None:
    writers = discover_writers()
    err = validate_requested_writers(_args(output=["none", "xteink"]), writers)
    assert err is not None
    assert "none" in err


def test_validate_unknown_writer() -> None:
    writers = discover_writers()
    err = validate_requested_writers(_args(output=["nope"]), writers)
    assert err is not None
    assert "nope" in err
    assert "xteink" in err


def test_validate_known_writer_ok() -> None:
    writers = discover_writers()
    assert validate_requested_writers(_args(xteink=True), writers) is None
    assert validate_requested_writers(_args(output=["xteink"]), writers) is None
    assert validate_requested_writers(_args(), writers) is None
    assert validate_requested_writers(_args(output=["none"]), writers) is None


def test_xteink_writer_enabled() -> None:
    writer = builtin_writers()["xteink"]
    cfg = _config()
    assert writer.enabled(_args(xteink=True), cfg) is True
    assert writer.enabled(_args(output=["xteink"]), cfg) is True
    assert writer.enabled(_args(), cfg) is True  # default-all
    assert writer.enabled(_args(output=["none"]), cfg) is False
    assert writer.enabled(_args(output=["json"]), cfg) is False


def test_run_enabled_writers_dry_run_skips_write(tmp_path: Path) -> None:
    writers = builtin_writers()
    cfg = _config(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    ctx = WriteContext(
        output_dir=out,
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=True,
    )
    paths = run_enabled_writers(
        writers,
        _empty_report(),
        ctx,
        args=_args(xteink=True),
        config=cfg,
    )
    assert paths == []
    assert list(out.iterdir()) == []


def test_run_enabled_writers_writes_xteink(tmp_path: Path) -> None:
    writers = builtin_writers()
    cfg = _config(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    generated_at = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    ctx = WriteContext(
        output_dir=out,
        generated_at=generated_at,
        max_display_links=8,
        dry_run=False,
    )
    paths = run_enabled_writers(
        writers,
        _empty_report(),
        ctx,
        args=_args(output=["xteink"]),
        config=cfg,
    )
    assert len(paths) == 1
    assert all(p.exists() for p in paths)
    assert paths[0].name.endswith(".xteink.md")


def test_run_enabled_writers_default_all_skips_epub_without_dep(
    tmp_path: Path,
) -> None:
    writers = builtin_writers()
    cfg = _config(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    ctx = WriteContext(
        output_dir=out,
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=False,
    )
    with patch(
        "rollup.addons.epub.ebooklib_available",
        return_value=False,
    ):
        paths = run_enabled_writers(
            writers,
            _empty_report(),
            ctx,
            args=_args(),  # default-all
            config=cfg,
        )
    assert paths
    assert any(p.suffix == ".txt" for p in paths)
    assert any(p.suffix == ".json" for p in paths)
    assert any(p.name.endswith(".xteink.md") for p in paths)
    assert not any(p.suffix == ".epub" for p in paths)


def test_run_enabled_writers_explicit_epub_fails_without_dep(
    tmp_path: Path,
) -> None:
    writers = builtin_writers()
    cfg = _config(tmp_path)
    ctx = WriteContext(
        output_dir=tmp_path / "out",
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=False,
    )
    with patch(
        "rollup.addons.epub.ebooklib_available",
        return_value=False,
    ):
        with pytest.raises(OutputWriterError, match="ebooklib"):
            run_enabled_writers(
                writers,
                _empty_report(),
                ctx,
                args=_args(output=["epub"]),
                config=cfg,
            )


def test_run_enabled_writers_none_writes_nothing(tmp_path: Path) -> None:
    writers = builtin_writers()
    cfg = _config(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    ctx = WriteContext(
        output_dir=out,
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=False,
    )
    paths = run_enabled_writers(
        writers,
        _empty_report(),
        ctx,
        args=_args(output=["none"]),
        config=cfg,
    )
    assert paths == []
    assert list(out.iterdir()) == []



def test_run_enabled_writers_propagates_failure(tmp_path: Path) -> None:
    class BoomWriter:
        name = "boom"

        def register_cli(self, parser: argparse.ArgumentParser) -> None:
            del parser

        def enabled(self, args: argparse.Namespace, config: Config) -> bool:
            del args, config
            return True

        def write(self, report, ctx: WriteContext) -> list[Path]:
            del report, ctx
            raise RuntimeError("nope")

    cfg = _config(tmp_path)
    ctx = WriteContext(
        output_dir=tmp_path / "out",
        generated_at=datetime.now(timezone.utc),
        max_display_links=8,
        dry_run=False,
    )
    with pytest.raises(OutputWriterError, match="boom"):
        run_enabled_writers(
            {"boom": BoomWriter()},
            _empty_report(),
            ctx,
            args=_args(),
            config=cfg,
        )


def test_discover_rejects_duplicate_entry_point_name() -> None:
    class FakeWriter:
        name = "xteink"

        def register_cli(self, parser: argparse.ArgumentParser) -> None:
            del parser

        def enabled(self, args: argparse.Namespace, config: Config) -> bool:
            del args, config
            return False

        def write(self, report, ctx: WriteContext) -> list[Path]:
            del report, ctx
            return []

    with patch(
        "rollup.output_writers._load_entry_point_writers",
        return_value={"xteink": FakeWriter()},
    ):
        with pytest.raises(OutputWriterError, match="Duplicate"):
            discover_writers()

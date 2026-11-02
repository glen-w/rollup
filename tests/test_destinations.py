"""OPDS / Calibre destinations and optional-writer classification."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.addons.calibre.writer import CalibreOutputWriter
from rollup.addons.opds import OpdsOutputWriter
from rollup.addons.opds.render import briefing_title, render_opds_atom
from rollup.config import Config
from rollup.models import DigestReport, DigestStats
from rollup.output_writers import (
    OutputWriterError,
    WriteContext,
    builtin_writers,
    run_enabled_writers,
    writer_is_required,
)
from rollup.user_config import UserConfigError, parse_toml_dict


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"xteink": False, "output": [], "calibre_drop_dir": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _config(tmp_path: Path, *, drop_dir: Path | None = None) -> Config:
    from rollup.addons.calibre.config import CalibreConfig

    root = tmp_path
    return Config(
        root=root / "news",
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
        calibre=CalibreConfig(drop_dir=drop_dir),
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


def test_digest_parser_registers_calibre_drop_dir() -> None:
    from rollup.cli_parser import build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["digest", "--calibre-drop-dir", "/tmp/calibre-drop", "--output", "none"]
    )
    assert args.calibre_drop_dir == "/tmp/calibre-drop"


def test_builtin_includes_destinations() -> None:
    writers = builtin_writers()
    assert "opds" in writers
    assert "calibre" in writers
    assert writer_is_required(writers["json"]) is True
    assert writer_is_required(writers["opds"]) is False
    assert writer_is_required(writers["calibre"]) is False


def test_opds_writer_writes_atom(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    generated = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    (out / "2026-07-02T103000Z-newsletter-digest.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    ctx = WriteContext(
        output_dir=out,
        generated_at=generated,
        max_display_links=8,
        dry_run=False,
        config=_config(tmp_path),
    )
    paths = OpdsOutputWriter().write(_empty_report(), ctx)
    assert len(paths) == 1
    xml = paths[0].read_text(encoding="utf-8")
    assert "application/atom+xml" in xml or "<feed " in xml
    assert "Rollup" in xml
    assert "newsletter-digest.html" in xml


def test_opds_title_uses_window() -> None:
    report = _empty_report()
    assert "2026-07-02" in briefing_title(report)


def test_calibre_skips_when_no_drop_dir_default_all(tmp_path: Path) -> None:
    writer = CalibreOutputWriter()
    cfg = _config(tmp_path)
    assert writer.enabled(_args(), cfg) is False


def test_calibre_copies_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "out"
    drop = tmp_path / "calibre-inbox"
    out.mkdir()
    generated = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    stem = "2026-07-02T103000Z-newsletter-digest"
    (out / f"{stem}.html").write_text("<html>hi</html>", encoding="utf-8")
    (out / f"{stem}.md").write_text("# hi", encoding="utf-8")
    cfg = _config(tmp_path, drop_dir=drop)
    ctx = WriteContext(
        output_dir=out,
        generated_at=generated,
        max_display_links=8,
        dry_run=False,
        explicit_outputs=True,
        config=cfg,
    )
    paths = CalibreOutputWriter().write(_empty_report(), ctx)
    names = {p.name for p in paths}
    assert f"{stem}.html" in names
    assert f"{stem}.md" in names
    assert (drop / f"{stem}.html").read_text(encoding="utf-8") == "<html>hi</html>"


def test_calibre_refuses_mail_root(tmp_path: Path) -> None:
    mail = tmp_path / "mail"
    mail.mkdir()
    drop = mail / "inside"
    out = tmp_path / "out"
    out.mkdir()
    cfg = _config(tmp_path, drop_dir=drop)
    ctx = WriteContext(
        output_dir=out,
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=False,
        explicit_outputs=True,
        config=cfg,
    )
    with pytest.raises(OutputWriterError, match="mail root"):
        CalibreOutputWriter().write(_empty_report(), ctx)


def test_optional_writer_failure_does_not_raise(tmp_path: Path) -> None:
    class BoomOptional:
        name = "boom"
        required = False

        def register_cli(self, parser: argparse.ArgumentParser) -> None:
            del parser

        def enabled(self, args: argparse.Namespace, config: Config) -> bool:
            del args, config
            return True

        def write(self, report, ctx: WriteContext) -> list[Path]:
            del report, ctx
            raise RuntimeError("nope")

    out = tmp_path / "out"
    out.mkdir()
    ctx = WriteContext(
        output_dir=out,
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=False,
    )
    batch = run_enabled_writers(
        {"boom": BoomOptional()},
        _empty_report(),
        ctx,
        args=_args(),
        config=_config(tmp_path),
    )
    assert batch.written == []
    assert len(batch.optional_failures) == 1
    assert batch.optional_failures[0][0] == "boom"


def test_parse_calibre_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    loaded = parse_toml_dict({"calibre": {"drop_dir": "/tmp/calibre"}}, path=path)
    assert loaded.calibre.drop_dir == Path("/tmp/calibre")
    with pytest.raises(UserConfigError, match="unknown"):
        parse_toml_dict({"calibre": {"watch": True}}, path=path)


def test_render_opds_atom_is_well_formed() -> None:
    xml = render_opds_atom(
        _empty_report(),
        Path("/tmp"),
        run_id_short="abcd1234",
        self_href="http://127.0.0.1:8765/opds",
    )
    assert xml.startswith("<?xml")
    assert "<id>urn:rollup:opds:abcd1234</id>" in xml

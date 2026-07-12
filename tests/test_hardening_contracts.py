"""0.4.2 hardening contracts for publication, state, and runtime edges."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import pytest

from rollup.clock import FixedClock
from rollup.config import Config
from rollup.doctor import run_doctor
from rollup.final_review import compute_digest_fingerprint
from rollup.models import FinalReviewResult
from rollup.pipeline import EXIT_FAILURE, EXIT_PARTIAL, run_digest
from rollup.publication import publish_latest_outputs
from rollup.run_lock import acquire_run_lock
from rollup.run_options import GroupingConfig, RunOptions


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _message(
    *,
    subject: str,
    body: str,
    message_id: str,
    dated: bool,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "Sender <sender@example.com>"
    msg["To"] = "reader@example.com"
    msg["Message-ID"] = message_id
    if dated:
        msg["Date"] = format_datetime(NOW)
    msg.set_content(body)
    return msg


def _write_mbox(path: Path, messages: list[EmailMessage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    for msg in messages:
        chunks.append("From - Sun Jul 12 12:00:00 2026\n")
        chunks.append(msg.as_string())
        if not chunks[-1].endswith("\n"):
            chunks.append("\n")
    path.write_text("".join(chunks), encoding="utf-8")


def _mail_tree(
    tmp_path: Path,
    *,
    include_undated: bool = True,
    subject_sentinel: str = "Dated hardening newsletter",
    body_sentinel: str = "Dated hardening body.",
) -> Path:
    root = tmp_path / "Newsletters.sbd"
    messages = [
        _message(
            subject=subject_sentinel,
            body=body_sentinel,
            message_id="<dated-hardening@example.com>",
            dated=True,
        )
    ]
    if include_undated:
        messages.append(
            _message(
                subject="Undated hardening newsletter",
                body="Undated body for seen-state contracts.",
                message_id="<undated-hardening@example.com>",
                dated=False,
            )
        )
    _write_mbox(root / "tech", messages)
    return root


def _config(tmp_path: Path, *, root: Path | None = None, **overrides) -> Config:
    root = root or _mail_tree(tmp_path)
    mail_root = tmp_path / "mail"
    mail_root.mkdir(exist_ok=True)
    base = dict(
        root=root,
        mail_root=mail_root,
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
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
    base.update(overrides)
    return Config(**base)


def _run(
    config: Config,
    *,
    run_options: RunOptions | None = None,
    grouping: GroupingConfig | None = None,
):
    return run_digest(
        config,
        run_options or RunOptions(write_manifest=True),
        grouping=grouping or GroupingConfig(enabled=False),
        manifest_config=None,
        clock=FixedClock(NOW),
    )


def _digest_files(output_dir: Path, suffix: str) -> list[Path]:
    return sorted(output_dir.glob(f"*-newsletter-digest*.{suffix}"))


def _seen_count(config: Config) -> int:
    if not config.db_path.exists():
        return 0
    conn = sqlite3.connect(config.db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM seen_messages").fetchone()[0])
    finally:
        conn.close()


def _passing_review(report) -> FinalReviewResult:
    fingerprint = compute_digest_fingerprint(report)
    return FinalReviewResult(
        overall_status="pass",
        safe_to_publish=True,
        issues=(),
        patches=(),
        review_source="ollama",
        profile_name="strict",
        model="llama3.2:3b",
        prompt_version="final_review_v1",
        generated_at=NOW,
        digest_fingerprint=fingerprint,
        review_input_hash="input-hash",
        echoed_digest_fingerprint=fingerprint,
        review_mode="report",
    )


def test_dated_success_latest_failure_partial(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def boom(**_kwargs):
        raise OSError("latest publish failed")

    monkeypatch.setattr("rollup.publication.publish_latest_outputs", boom)

    result = _run(config, run_options=RunOptions(publish_latest=True))

    assert result.exit_code == EXIT_PARTIAL
    assert _digest_files(config.output_dir, "md")
    assert _digest_files(config.output_dir, "html")
    assert result.aggregated.publication_failed is True
    assert result.aggregated.seen_state_updated is True
    assert _seen_count(config) == 1


def test_dated_success_manifest_failure_partial(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def boom(self, *, update_latest: bool = False):
        raise OSError("manifest write failed")

    monkeypatch.setattr(
        "rollup.manifest.ManifestBuilder.write_if_state_writable",
        boom,
    )

    result = _run(config, run_options=RunOptions(write_manifest=True))

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.manifest_write_failed is True
    assert _digest_files(config.output_dir, "md")
    assert _digest_files(config.output_dir, "html")


def test_dated_success_seen_failure_partial(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("seen table locked")

    monkeypatch.setattr("rollup.state.upsert_seen_keys", boom)

    result = _run(config)

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.seen_state_failed is True
    assert _digest_files(config.output_dir, "md")
    assert _digest_files(config.output_dir, "html")
    assert _seen_count(config) == 0


def test_dated_write_failure_zero_seen_updates(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("dated write failed")

    monkeypatch.setattr("rollup.pipeline.atomic_write_digest", boom)

    result = _run(config)

    assert result.exit_code == EXIT_FAILURE
    assert _seen_count(config) == 0


def test_atomic_write_digest_fails_first_format(tmp_path: Path, monkeypatch) -> None:
    from rollup.render import atomic_write_digest

    original_rename = Path.rename

    def fail_md(self: Path, target: Path):
        if self.name.endswith(".md"):
            raise OSError("fail first format")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_md)

    with pytest.raises(OSError, match="first format"):
        atomic_write_digest(tmp_path, NOW, "# md\n", "<html></html>")

    assert not _digest_files(tmp_path, "md")
    assert not _digest_files(tmp_path, "html")


def test_atomic_write_digest_fails_second_format(tmp_path: Path, monkeypatch) -> None:
    from rollup.render import atomic_write_digest

    original_rename = Path.rename

    def fail_html(self: Path, target: Path):
        if self.name.endswith(".html"):
            raise OSError("fail second format")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_html)

    with pytest.raises(OSError, match="second format"):
        atomic_write_digest(tmp_path, NOW, "# md\n", "<html></html>")

    assert not _digest_files(tmp_path, "md")
    assert not _digest_files(tmp_path, "html")


def test_latest_pair_atomicity_no_split_generation(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    output.mkdir()
    old_md = output / "latest.md"
    old_html = output / "latest.html"
    old_md.write_text("generation=old\n", encoding="utf-8")
    old_html.write_text("generation=old\n", encoding="utf-8")
    new_md = output / "2026-newsletter-digest-new.md"
    new_html = output / "2026-newsletter-digest-new.html"
    new_md.write_text("generation=new\n", encoding="utf-8")
    new_html.write_text("generation=new\n", encoding="utf-8")

    original_replace = Path.replace

    def fail_second_commit(self: Path, target: Path):
        if self.name.startswith(".tmp-latest.html") and target.name == "latest.html":
            raise OSError("fail second latest commit")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second_commit)

    with pytest.raises(OSError, match="second latest"):
        publish_latest_outputs(
            output_dir=output,
            md_path=new_md,
            html_path=new_html,
            run_status="success",
            publish_latest=True,
        )

    assert old_md.read_text(encoding="utf-8") == "generation=old\n"
    assert old_html.read_text(encoding="utf-8") == "generation=old\n"


def test_sidecar_write_failure_partial_dated_ok(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, final_review_enabled=True)

    monkeypatch.setattr(
        "rollup.final_review.execute_final_review",
        lambda report, *_args, **_kwargs: _passing_review(report),
    )

    def boom(*_args, **_kwargs):
        raise OSError("sidecar write failed")

    monkeypatch.setattr("rollup.final_review.write_final_review_report", boom)

    result = _run(config)

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.final_review_failed is True
    assert _digest_files(config.output_dir, "md")
    assert _digest_files(config.output_dir, "html")


def test_programmatic_run_options_dry_run(tmp_path: Path, monkeypatch) -> None:
    config = _config(
        tmp_path,
        no_ollama=False,
        final_review_enabled=True,
        group_summaries_enabled=True,
    )

    def fail_network(*_args, **_kwargs):
        raise AssertionError("network should not be reached during dry-run")

    monkeypatch.setattr("rollup.summarize.check_ollama_available", fail_network)
    monkeypatch.setattr("rollup.final_review.call_final_review_model", fail_network)
    monkeypatch.setattr("rollup.group_summarize._call_ollama_for_group", fail_network)

    result = _run(
        config,
        run_options=RunOptions(dry_run=True, write_manifest=False),
        grouping=GroupingConfig(enabled=True),
    )

    assert result.exit_code == 0
    assert result.status == "dry_run"
    assert not config.output_dir.exists()
    assert not config.state_dir.exists()


def test_doctor_default_no_network_no_write(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    before_mail = sorted(config.mail_root.rglob("*"))

    def fail_network(*_args, **_kwargs):
        raise AssertionError("doctor default should not probe network")

    monkeypatch.setattr("requests.get", fail_network)

    report = run_doctor(config, RunOptions(), full=False, network=False)

    assert report.ok
    assert sorted(config.mail_root.rglob("*")) == before_mail


def test_lock_contention_digest_exit_1(tmp_path: Path) -> None:
    config = _config(tmp_path)
    lock = acquire_run_lock(
        config.state_dir, "already-running", started_at=datetime.now(timezone.utc)
    )
    try:
        result = _run(config)
    finally:
        lock.release()

    assert result.exit_code == EXIT_FAILURE
    assert result.aggregated.hard_failure is True
    assert "Another state operation" in (result.error_message or "")


def test_allow_partial_latest_true_updates_latest(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, final_review_enabled=True)
    monkeypatch.setattr(
        "rollup.final_review.execute_final_review",
        lambda report, *_args, **_kwargs: _passing_review(report),
    )
    monkeypatch.setattr(
        "rollup.final_review.write_final_review_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("sidecar failed")),
    )

    result = _run(
        config,
        run_options=RunOptions(publish_latest=True, allow_partial_latest=True),
    )

    assert result.exit_code == EXIT_PARTIAL
    assert result.aggregated.latest_outputs_updated is True
    assert (config.output_dir / "latest.md").exists()
    assert (config.output_dir / "latest.html").exists()


def test_privacy_no_corpus_in_logs_or_manifests(tmp_path: Path) -> None:
    from rollup import cli

    subject_sentinel = "PRIVACY_SUBJECT_SENTINEL_042"
    body_sentinel = "PRIVACY_BODY_SENTINEL_042"
    root = _mail_tree(
        tmp_path,
        include_undated=False,
        subject_sentinel=subject_sentinel,
        body_sentinel=body_sentinel,
    )
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "digest",
            "--root",
            str(root),
            "--mail-root",
            str(tmp_path / "mail"),
            "--output-dir",
            str(tmp_path / "output"),
            "--state-dir",
            str(tmp_path / "state"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )

    assert cli.cmd_digest(args) == 0

    manifest_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "state" / "manifests").glob("*.json"))
    )
    log_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "logs").glob("*.log"))
    )

    for text in (manifest_text, log_text):
        assert subject_sentinel not in text
        assert body_sentinel not in text
    assert json.loads(next((tmp_path / "state" / "manifests").glob("*.json")).read_text())

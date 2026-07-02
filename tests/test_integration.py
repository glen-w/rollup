"""Integration tests for full digest pipeline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
PROJECT_ROOT = Path(__file__).parent.parent


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rollup", *args],
        cwd=cwd or PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_inventory_fixture(tmp_path: Path) -> None:
    result = _run("inventory", "--root", str(FIXTURE_ROOT))
    assert result.returncode == 0
    assert "brainfood" in result.stdout
    assert "tech" in result.stdout


def test_digest_default_fixture(tmp_path: Path) -> None:
    """Default digest (no flags) matches explicit --no-ollama: writes output, no Ollama."""
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0, result.stderr
    assert "no_ollama=True" in result.stderr
    combined = result.stdout + result.stderr
    assert "Summaries: Ollama 0" in combined
    md_files = list(output.glob("*-newsletter-digest.md"))
    html_files = list(output.glob("*-newsletter-digest.html"))
    assert len(md_files) == 1
    assert len(html_files) == 1
    assert md_files[0].stat().st_size > 0
    assert html_files[0].stat().st_size > 0


def test_digest_no_ollama_fixture(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0, result.stderr
    md_files = list(output.glob("*-newsletter-digest.md"))
    html_files = list(output.glob("*-newsletter-digest.html"))
    assert len(md_files) == 1
    assert len(html_files) == 1
    assert (
        "Undated" in md_files[0].read_text(encoding="utf-8")
        or "undated" in md_files[0].read_text(encoding="utf-8").lower()
    )
    html = html_files[0].read_text(encoding="utf-8")
    assert "class='rollup-toc'" in html
    assert "<details class='run-details'>" in html


def test_digest_dry_run_no_writes(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--dry-run",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--log-dir",
        str(logs),
        "--mail-root",
        str(tmp_path / "mail"),
        "--verbose",
    )
    assert result.returncode == 0, result.stderr
    assert not output.exists()
    assert not state.exists()
    assert not logs.exists()
    assert "Dry run" in result.stderr


def test_inventory_json_out_rejected_in_mail_root(tmp_path: Path) -> None:
    mail = tmp_path / "gmail"
    mail.mkdir()
    result = _run(
        "inventory",
        "--root",
        str(FIXTURE_ROOT),
        "--mail-root",
        str(mail),
        "--json-out",
        str(mail / "inventory.json"),
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
    )
    assert result.returncode != 0
    assert "mail root" in result.stderr.lower() or "ERROR" in result.stderr


def test_inventory_stdout_table_columns() -> None:
    result = _run("inventory", "--root", str(FIXTURE_ROOT))
    assert result.returncode == 0
    assert "msgs=" in result.stdout
    assert "KB" in result.stdout


def test_inventory_json_out(tmp_path: Path) -> None:
    json_path = tmp_path / "inventory.json"
    result = _run(
        "inventory",
        "--root",
        str(FIXTURE_ROOT),
        "--json-out",
        str(json_path),
        "--mail-root",
        str(tmp_path / "mail"),
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
    )
    assert result.returncode == 0, result.stderr
    assert json_path.exists()
    assert "folder_name" in json_path.read_text(encoding="utf-8")


def test_digest_exclude_folder(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--exclude-folder",
        "hoops",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0
    md = (
        list(output.glob("*-newsletter-digest.md"))[0]
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "hoops" not in md


def test_digest_stats_in_stdout(tmp_path: Path) -> None:
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--dry-run",
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0
    for field in (
        "Folders scanned:",
        "Messages parsed:",
        "Dated included:",
        "Undated needing review:",
        "Skipped outside window:",
        "Skipped seen undated:",
        "Parse errors:",
        "Summaries:",
    ):
        assert field in result.stdout


def test_safety_rejects_state_in_mail_root(tmp_path: Path) -> None:
    mail = tmp_path / "gmail"
    mail.mkdir()
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--mail-root",
        str(mail),
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(mail / "state"),
    )
    assert result.returncode != 0


def test_seen_undated_skipped_on_second_run(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    common = [
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--folder",
        "misc",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    ]
    first = _run(*common)
    assert first.returncode == 0, first.stderr
    assert "Undated needing review:" in first.stdout
    first_undated = int(
        next(
            line.split(":")[1].strip()
            for line in first.stdout.splitlines()
            if line.startswith("Undated needing review:")
        )
    )
    assert first_undated >= 1

    second = _run(*common)
    assert second.returncode == 0, second.stderr
    second_undated = int(
        next(
            line.split(":")[1].strip()
            for line in second.stdout.splitlines()
            if line.startswith("Undated needing review:")
        )
    )
    skipped_seen = int(
        next(
            line.split(":")[1].strip()
            for line in second.stdout.splitlines()
            if line.startswith("Skipped seen undated:")
        )
    )
    assert second_undated == 0
    assert skipped_seen >= 1

    third = _run(*common, "--include-seen-undated")
    assert third.returncode == 0
    third_undated = int(
        next(
            line.split(":")[1].strip()
            for line in third.stdout.splitlines()
            if line.startswith("Undated needing review:")
        )
    )
    assert third_undated >= 1


def test_partial_write_does_not_update_seen_state(tmp_path: Path, monkeypatch) -> None:
    from rollup import cli

    output = tmp_path / "output"
    state = tmp_path / "state"

    def boom(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(cli, "atomic_write_digest", boom)

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "digest",
            "--root",
            str(FIXTURE_ROOT),
            "--no-ollama",
            "--folder",
            "misc",
            "--output-dir",
            str(output),
            "--state-dir",
            str(state),
            "--mail-root",
            str(tmp_path / "mail"),
        ]
    )
    assert cli.cmd_digest(args) == 1
    db = state / "rollup.db"
    if db.exists():
        import sqlite3

        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM seen_messages").fetchone()[0]
        conn.close()
        assert count == 0


def test_digest_folder_filter(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--folder",
        "tech",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0
    md = list(output.glob("*-newsletter-digest.md"))[0].read_text(encoding="utf-8")
    assert "tech" in md.lower()
    assert "brainfood" not in md.lower()


def test_digest_trackerwall_renders_readable_links(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--folder",
        "trackerwall",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0, result.stderr
    md = list(output.glob("*-newsletter-digest.md"))[0].read_text(encoding="utf-8")
    html = list(output.glob("*-newsletter-digest.html"))[0].read_text(encoding="utf-8")

    assert "[Open post](" in md
    assert "[Register](" in md or "[Register for Teams event](" in md
    assert "- <https://" not in md

    assert ">Open post<" in html
    assert "Register for Teams event" in html or ">Register<" in html
    assert ">https://substack.com/app-link/post?" not in html
    assert ">https://u14608870.ct.sendgrid.net/ls/click?" not in html
    assert "eotrx.substackcdn.com/o/abc/p.gif" not in html


def test_safety_rejects_output_in_mail_root(tmp_path: Path) -> None:
    mail = tmp_path / "gmail"
    mail.mkdir()
    newsletters = mail / "Newsletters.sbd"
    newsletters.mkdir()
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--mail-root",
        str(mail),
        "--output-dir",
        str(mail / "output"),
        "--state-dir",
        str(tmp_path / "state"),
    )
    assert result.returncode != 0
    assert "mail root" in result.stderr.lower() or "ERROR" in result.stderr


def _digest_args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        *extra,
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
        "--mail-root",
        str(tmp_path / "mail"),
    ]


def _parse_summary_stats(stdout: str) -> tuple[int, int, int]:
    for line in stdout.splitlines():
        if line.startswith("Summaries:"):
            parts = line.replace("Summaries:", "").strip().split("·")
            ollama = int(parts[0].strip().split()[-1])
            cache = int(parts[1].strip().split()[-1])
            fallback = int(parts[2].strip().split()[-1])
            return ollama, cache, fallback
    raise AssertionError("Summaries line not found in stdout")


def _assert_summary_source_consistency(entries: list) -> None:
    from rollup.filter import count_summary_sources

    ollama, cache, fallback = count_summary_sources(entries)
    none_count = sum(1 for e in entries if e.summary_source == "none")
    assert ollama + cache + fallback + none_count == len(entries)


def test_digest_ollama_mocked_fixture(tmp_path: Path, monkeypatch) -> None:
    from rollup import cli
    from rollup.summarize import apply_summaries as real_apply_summaries

    captured_entries: list = []
    summarize_calls = {"count": 0}

    def mock_summarize(classified, *args, **kwargs):
        summarize_calls["count"] += 1
        return f"MOCK: {classified.parsed.subject}"

    def tracking_apply(entries, *args, **kwargs):
        result = real_apply_summaries(entries, *args, **kwargs)
        captured_entries.extend(result)
        return result

    monkeypatch.setattr(
        "rollup.summarize.check_ollama_available", lambda *a, **k: (True, "ok")
    )
    monkeypatch.setattr("rollup.summarize.summarize_message", mock_summarize)
    monkeypatch.setattr("rollup.summarize.apply_summaries", tracking_apply)

    parser = cli.build_parser()
    args = parser.parse_args(
        _digest_args(
            tmp_path, "--ollama", "--folder", "tech", "--lookback-days", "36500"
        )
    )

    import io
    import sys

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        rc = cli.cmd_digest(args)
    finally:
        sys.stdout = old_stdout
    first_stdout = buf.getvalue()
    assert rc == 0

    output = tmp_path / "output"
    md_files = list(output.glob("*-newsletter-digest.md"))
    html_files = list(output.glob("*-newsletter-digest.html"))
    assert len(md_files) == 1
    assert len(html_files) == 1
    md_text = md_files[0].read_text(encoding="utf-8")
    assert "MOCK:" in md_text

    ollama1, cache1, fallback1 = _parse_summary_stats(first_stdout)
    assert ollama1 > 0
    assert cache1 == 0
    _assert_summary_source_consistency(captured_entries)

    import sqlite3

    conn = sqlite3.connect(tmp_path / "state" / "rollup.db")
    count = conn.execute("SELECT COUNT(*) FROM summary_generations").fetchone()[0]
    conn.close()
    assert count >= ollama1

    calls_after_first = summarize_calls["count"]

    buf2 = io.StringIO()
    sys.stdout = buf2
    try:
        rc2 = cli.cmd_digest(
            parser.parse_args(
                _digest_args(
                    tmp_path, "--ollama", "--folder", "tech", "--lookback-days", "36500"
                )
            )
        )
    finally:
        sys.stdout = old_stdout
    assert rc2 == 0
    ollama2, cache2, _ = _parse_summary_stats(buf2.getvalue())
    assert cache2 > 0
    assert ollama2 == 0
    assert summarize_calls["count"] == calls_after_first


def test_digest_ollama_dry_run_no_network(tmp_path: Path, monkeypatch) -> None:
    from rollup import cli

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Ollama path should not run during dry-run")

    monkeypatch.setattr("rollup.summarize.check_ollama_available", fail_if_called)
    monkeypatch.setattr("rollup.summarize.summarize_message", fail_if_called)

    parser = cli.build_parser()
    args = parser.parse_args(_digest_args(tmp_path, "--ollama", "--dry-run"))
    rc = cli.cmd_digest(args)
    assert rc == 0
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "state").exists()


def test_digest_ollama_rejects_remote_url(tmp_path: Path, monkeypatch, capsys) -> None:
    from rollup import cli

    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "Should not reach Ollama network/cache after URL validation"
        )

    monkeypatch.setattr("rollup.summarize.check_ollama_available", fail_if_called)
    monkeypatch.setattr("rollup.summarize.summarize_message", fail_if_called)
    monkeypatch.setattr("rollup.state.store_summary", fail_if_called)

    parser = cli.build_parser()
    args = parser.parse_args(
        _digest_args(
            tmp_path,
            "--ollama",
            "--ollama-url",
            "http://192.168.1.1:11434/api/generate",
        )
    )
    rc = cli.cmd_digest(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR:" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("ollama_flag", [[], ["--no-ollama"]])
def test_digest_default_path_skips_ollama(
    tmp_path: Path, monkeypatch, ollama_flag: list[str]
) -> None:
    from rollup import cli

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Ollama helpers must not run without --ollama")

    monkeypatch.setattr("rollup.summarize.validate_ollama_url", fail_if_called)
    monkeypatch.setattr("rollup.summarize.check_ollama_available", fail_if_called)
    monkeypatch.setattr("rollup.summarize.summarize_message", fail_if_called)

    parser = cli.build_parser()
    extra = list(ollama_flag) + ["--dry-run"]
    args = parser.parse_args(_digest_args(tmp_path, *extra))
    rc = cli.cmd_digest(args)
    assert rc == 0


def test_list_newsletter_types(tmp_path: Path) -> None:
    result = _run(
        "digest",
        "--list-newsletter-types",
        "--dry-run",
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0
    assert "unclassified" in result.stdout


def test_list_summary_profiles(tmp_path: Path) -> None:
    result = _run(
        "digest",
        "--list-summary-profiles",
        "--dry-run",
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0
    assert "rough:" in result.stdout


def test_digest_summary_variants_writes_multiple_outputs(
    tmp_path: Path, monkeypatch
) -> None:
    from rollup import cli

    monkeypatch.setattr(
        "rollup.summarize.check_ollama_available", lambda *a, **k: (True, "ok")
    )
    monkeypatch.setattr(
        "rollup.summarize.summarize_message",
        lambda classified, *args, **kwargs: f"MOCK: {classified.parsed.subject}",
    )
    parser = cli.build_parser()
    args = parser.parse_args(
        _digest_args(
            tmp_path,
            "--ollama",
            "--summary-variants",
            "rough,deep",
            "--folder",
            "tech",
            "--lookback-days",
            "36500",
        )
    )
    rc = cli.cmd_digest(args)
    assert rc == 0
    assert list((tmp_path / "output").glob("*-newsletter-digest.rough.md"))
    assert list((tmp_path / "output").glob("*-newsletter-digest.deep.md"))

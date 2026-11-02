"""``rollup init``: discovery, prompts, dry-run, merge, mail-root refusal, non-interactive."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rollup.cli import build_parser
from rollup.init_cmd import EXIT_INVALID, EXIT_OK, cmd_init, render_plan_toml
from rollup.user_config import load_user_config

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
PROJECT_ROOT = Path(__file__).parent.parent


def _args(*argv: str):
    return build_parser().parse_args(["init", *argv])


class _Script:
    """Scripted stdin: answers consumed in order; records every prompt."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, question: str) -> str:
        self.prompts.append(question)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {question!r}")
        return self.answers.pop(0)


def _fake_home(tmp_path: Path, *accounts: str) -> Path:
    home = tmp_path / "home"
    for account in accounts:
        (home / "Library" / "Thunderbird" / "Profiles" / "abc.default" / "Mail" / account / "Newsletters.sbd").mkdir(parents=True)
    return home


def _common(tmp_path: Path) -> list[str]:
    return [
        "--state-dir", str(tmp_path / "state"),
        "--output-dir", str(tmp_path / "out"),
        "--log-dir", str(tmp_path / "logs"),
        "--skip-doctor",
    ]


def test_dry_run_writes_nothing_and_prints_toml(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    out: list[str] = []
    rc = cmd_init(
        _args("--yes", "--dry-run", "--root", str(FIXTURE_ROOT), *_common(tmp_path)),
        prompt=_Script(),
        emit=out.append,
    )
    assert rc == EXIT_OK
    assert not cfg.exists()
    text = "\n".join(out)
    assert "# would write to" in text
    assert f'root = "{FIXTURE_ROOT}"' in text
    assert f'mail_root = "{FIXTURE_ROOT.parent}"' in text
    assert 'profile = "weekly"' in text
    assert "ollama = false" in text


def test_yes_writes_file_with_inferred_mail_root(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "cfg" / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    rc = cmd_init(
        _args("--yes", "--root", str(FIXTURE_ROOT), *_common(tmp_path)),
        prompt=_Script(),
        emit=lambda _s: None,
    )
    assert rc == EXIT_OK
    loaded = load_user_config(explicit_path=str(cfg))
    assert loaded.values["root"] == str(FIXTURE_ROOT)
    assert loaded.values["mail_root"] == str(FIXTURE_ROOT.parent)
    assert loaded.values["state_dir"] == str(tmp_path / "state")
    assert loaded.values["ollama"] is False
    assert loaded.values["lookback_days"] == 7


def test_single_discovered_tree_is_offered_and_accepted(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    home = _fake_home(tmp_path, "imap.example.com")
    expected = home / "Library/Thunderbird/Profiles/abc.default/Mail/imap.example.com/Newsletters.sbd"
    # confirm tree, state, output, log, profile choice, lookback, ollama?, write?
    script = _Script("y", "", "", "", "", "", "n", "y")
    out: list[str] = []
    rc = cmd_init(_args(*_common(tmp_path)), prompt=script, emit=out.append, home=home)
    assert rc == EXIT_OK, out
    loaded = load_user_config(explicit_path=str(cfg))
    assert Path(loaded.values["root"]) == expected.resolve()
    assert Path(loaded.values["mail_root"]) == expected.resolve().parent
    assert any("Found Thunderbird newsletters" in line for line in out)


def test_multiple_discovered_trees_prompt_for_choice(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    home = _fake_home(tmp_path, "a.example.com", "b.example.com")
    # choose "2", dirs x3, profile, lookback, ollama n, write y
    script = _Script("2", "", "", "", "", "", "n", "y")
    rc = cmd_init(_args(*_common(tmp_path)), prompt=script, emit=lambda _s: None, home=home)
    assert rc == EXIT_OK
    loaded = load_user_config(explicit_path=str(cfg))
    assert "b.example.com" in loaded.values["root"]


def test_undiscoverable_paths_ask_and_accept_typed_path(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    home = tmp_path / "empty-home"
    home.mkdir()
    script = _Script(str(FIXTURE_ROOT), "", "", "", "", "", "n", "y")
    out: list[str] = []
    rc = cmd_init(_args(*_common(tmp_path)), prompt=script, emit=out.append, home=home)
    assert rc == EXIT_OK, out
    assert any("No Thunderbird Newsletters.sbd tree was discovered" in line for line in out)
    loaded = load_user_config(explicit_path=str(cfg))
    assert loaded.values["root"] == str(FIXTURE_ROOT)


def test_gmail_only_setup_without_local_mail(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    home = tmp_path / "empty-home"
    home.mkdir()
    # blank path → skip local mail; gmail? y; prefix default; dirs; profile; lookback; ollama n; write y
    script = _Script("", "y", "", "", "", "", "", "", "n", "y")
    out: list[str] = []
    rc = cmd_init(_args(*_common(tmp_path)), prompt=script, emit=out.append, home=home)
    assert rc == EXIT_OK, out
    loaded = load_user_config(explicit_path=str(cfg))
    assert "root" not in loaded.values
    assert loaded.gmail.enabled is True
    assert loaded.gmail.label_prefix in ("Newsletters", "Newsletters/")
    assert any("rollup gmail auth" in line for line in out)


def test_gmail_flag_non_interactive(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    home = tmp_path / "empty-home"
    home.mkdir()
    rc = cmd_init(
        _args("--yes", "--gmail-label-prefix", "News/", *_common(tmp_path)),
        prompt=_Script(),
        emit=lambda _s: None,
        home=home,
    )
    assert rc == EXIT_OK
    loaded = load_user_config(explicit_path=str(cfg))
    assert loaded.gmail.label_prefix in ("News", "News/")


def test_nothing_to_digest_is_an_error(tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    home = tmp_path / "empty-home"
    home.mkdir()
    script = _Script("", "n")  # no path, no gmail
    rc = cmd_init(_args(*_common(tmp_path)), prompt=script, emit=lambda _s: None, home=home)
    assert rc == EXIT_INVALID
    assert "Nothing to digest" in capsys.readouterr().err
    assert not cfg.exists()


@pytest.mark.parametrize("flag", ["--state-dir", "--output-dir", "--log-dir"])
def test_refuses_writable_dir_inside_mail_root(tmp_path: Path, monkeypatch, capsys, flag: str) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    mail = tmp_path / "mail"
    root = mail / "Newsletters.sbd"
    root.mkdir(parents=True)
    argv = ["--yes", "--root", str(root), *_common(tmp_path)]
    argv[argv.index(flag) + 1] = str(mail / "inside")
    rc = cmd_init(_args(*argv), prompt=_Script(), emit=lambda _s: None)
    assert rc == EXIT_INVALID
    assert "inside the mail root" in capsys.readouterr().err
    assert not cfg.exists()


def test_merge_preserves_unrelated_keys_and_backs_up(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    cfg.write_text(
        'ollama_model = "custom:1b"\nlookback_days = 30\n\n[folders.tech]\nemoji = "T"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    rc = cmd_init(
        _args("--yes", "--root", str(FIXTURE_ROOT), "--lookback-days", "14", *_common(tmp_path)),
        prompt=_Script(),
        emit=lambda _s: None,
    )
    assert rc == EXIT_OK
    text = cfg.read_text(encoding="utf-8")
    assert 'ollama_model = "custom:1b"' in text
    assert 'emoji = "T"' in text
    loaded = load_user_config(explicit_path=str(cfg))
    assert loaded.values["lookback_days"] == 14
    assert loaded.values["root"] == str(FIXTURE_ROOT)
    assert (tmp_path / "rollup.toml.bak").exists()


def test_declining_write_leaves_no_file(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    script = _Script("", "", "", "", "", "n", "n")  # dirs, profile, lookback, ollama n, write n
    rc = cmd_init(_args("--root", str(FIXTURE_ROOT), *_common(tmp_path)), prompt=script, emit=lambda _s: None)
    assert rc == 1
    assert not cfg.exists()


def test_invalid_lookback_and_profile_are_rejected(tmp_path: Path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    rc = cmd_init(
        _args("--yes", "--root", str(FIXTURE_ROOT), "--lookback-days", "0", *_common(tmp_path)),
        prompt=_Script(), emit=lambda _s: None,
    )
    assert rc == EXIT_INVALID and "lookback_days" in capsys.readouterr().err
    rc = cmd_init(
        _args("--yes", "--root", str(FIXTURE_ROOT), "--profile", "hourly", *_common(tmp_path)),
        prompt=_Script(), emit=lambda _s: None,
    )
    assert rc == EXIT_INVALID and "run profile" in capsys.readouterr().err
    assert not cfg.exists()


def test_ollama_probe_only_when_requested(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    probes: list[tuple[str, str]] = []

    def probe(url: str, model: str) -> tuple[bool, str]:
        probes.append((url, model))
        return True, "ok"

    # --yes never probes (default answer is no).
    rc = cmd_init(
        _args("--yes", "--ollama", "--ollama-model", "m:1b", "--root", str(FIXTURE_ROOT), *_common(tmp_path)),
        prompt=_Script(), emit=lambda _s: None, ollama_probe=probe,
    )
    assert rc == EXIT_OK
    assert probes == []
    loaded = load_user_config(explicit_path=str(cfg))
    assert loaded.values["ollama"] is True
    assert loaded.values["ollama_model"] == "m:1b"

    # Interactive: model prompt default, probe y, write y.
    cfg.unlink()
    script = _Script("", "", "", "", "", "y", "", "y", "y")
    out: list[str] = []
    rc = cmd_init(_args("--root", str(FIXTURE_ROOT), *_common(tmp_path)), prompt=script, emit=out.append, ollama_probe=probe)
    assert rc == EXIT_OK, out
    assert probes and probes[0][0].startswith("http://localhost")
    assert any("ollama probe: ok" in line for line in out)


def test_default_digest_after_init_makes_no_network_calls(tmp_path: Path, monkeypatch) -> None:
    """The file init writes must keep the contract default: no network on `rollup digest`."""
    import socket

    cfg = tmp_path / "rollup.toml"
    monkeypatch.setattr("rollup.config_service.resolve_config_path", lambda *, explicit=None: cfg)
    rc = cmd_init(
        _args("--yes", "--root", str(FIXTURE_ROOT), *_common(tmp_path)),
        prompt=_Script(), emit=lambda _s: None,
    )
    assert rc == EXIT_OK

    def no_net(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    result = subprocess.run(
        [sys.executable, "-m", "rollup", "--config", str(cfg), "digest", "--lookback-days", "3650", "--dry-run"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "no_ollama=True" in result.stderr


def test_render_plan_toml_roundtrips_through_loader(tmp_path: Path) -> None:
    from rollup.init_cmd import InitPlan

    plan = InitPlan(
        config_path=tmp_path / "x.toml", mode="mbox", root=FIXTURE_ROOT, mail_root=FIXTURE_ROOT.parent,
        state_dir=tmp_path / "s", output_dir=tmp_path / "o", log_dir=tmp_path / "l",
        profile="daily", lookback_days=1, ollama=False, ollama_model=None,
    )
    (tmp_path / "x.toml").write_text(render_plan_toml(plan), encoding="utf-8")
    loaded = load_user_config(explicit_path=str(tmp_path / "x.toml"))
    assert loaded.values["profile"] == "daily"
    assert loaded.values["lookback_days"] == 1


def test_cli_init_tolerates_missing_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "new" / "rollup.toml"
    result = subprocess.run(
        [sys.executable, "-m", "rollup", "--config", str(cfg), "init", "--yes", "--dry-run",
         "--root", str(FIXTURE_ROOT), *_common(tmp_path)],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "# would write to" in result.stdout
    assert not cfg.exists()

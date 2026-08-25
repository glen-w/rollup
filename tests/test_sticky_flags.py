"""Tests for sticky-key ↔ CLI flag registry."""

from __future__ import annotations

import argparse

from rollup.sticky_flags import (
    STICKY_FLAG_SPECS,
    apply_sticky_specs,
    assert_sticky_keys_covered,
    sticky_to_argv,
)
from rollup.user_config import STICKY_KEYS, apply_sticky_to_namespace


def test_sticky_keys_registry_covers_all() -> None:
    assert_sticky_keys_covered()
    mapped = {s.key for s in STICKY_FLAG_SPECS}
    assert "profile" not in mapped  # NON_CLI
    assert "lookback_days" in mapped
    assert "ollama_model" in mapped
    assert "summary_profile" in mapped
    assert mapped | {"profile"} == STICKY_KEYS


def test_sticky_to_argv_bool_pairs_and_output() -> None:
    argv = sticky_to_argv(
        {
            "lookback_days": 3,
            "root": "~/mail/Newsletters.sbd",
            "ollama": False,
            "no_grouping": True,
            "output": ["json", "txt"],
            "effort": "light",
        }
    )
    assert "--lookback-days" in argv and "3" in argv
    assert "--no-ollama" in argv
    assert "--ollama" not in argv
    assert "--no-grouping" in argv
    assert "--output" in argv and "json" in argv and "txt" in argv
    assert "--effort" in argv and "light" in argv


def test_sticky_to_argv_llm_provider_and_model() -> None:
    argv = sticky_to_argv(
        {
            "llm_provider": "litellm",
            "llm_model": "openai/gpt-4o",
            "ollama": True,
        }
    )
    assert "--llm-provider" in argv
    assert argv[argv.index("--llm-provider") + 1] == "litellm"
    assert "--llm-model" in argv
    assert argv[argv.index("--llm-model") + 1] == "openai/gpt-4o"
    assert "--ollama" in argv


def test_sticky_to_argv_default_all_omits_output() -> None:
    argv = sticky_to_argv({"output": ["all"], "ollama": True, "no_grouping": False})
    assert "--ollama" in argv
    assert "--grouping" in argv
    assert "--output" not in argv


def test_apply_sticky_specs_respects_require_attr() -> None:
    args = argparse.Namespace(lookback_days=7)
    apply_sticky_specs(
        args,
        {"ollama_model": "llama3.2", "lookback_days": 1},
        argv=["digest"],
    )
    assert args.lookback_days == 1
    assert not hasattr(args, "ollama_model")

    args2 = argparse.Namespace(lookback_days=7, ollama_model=None)
    apply_sticky_specs(
        args2,
        {"ollama_model": "llama3.2"},
        argv=["digest"],
    )
    assert args2.ollama_model == "llama3.2"


def test_apply_sticky_to_namespace_delegates() -> None:
    args = argparse.Namespace(
        lookback_days=7,
        effort=None,
        folder=None,
        root="/default",
        output=[],
        ollama=False,
        no_ollama=True,
        no_grouping=False,
        grouping=True,
    )
    apply_sticky_to_namespace(
        args,
        {
            "lookback_days": 2,
            "effort": "high",
            "folder": ["tech"],
            "ollama": True,
            "no_grouping": True,
            "output": ["none"],
        },
        argv=["digest"],
    )
    assert args.lookback_days == 2
    assert args.effort == "high"
    assert args.folder == ["tech"]
    assert args.ollama is True
    assert args.no_ollama is False
    assert args.no_grouping is True
    assert args.output == ["none"]


def test_sticky_to_argv_expands_user_paths() -> None:
    from pathlib import Path

    argv = sticky_to_argv({"root": "~/Newsletters.sbd", "mail_root": "~/mail"})
    root_idx = argv.index("--root")
    mail_idx = argv.index("--mail-root")
    assert argv[root_idx + 1] == str(Path("~/Newsletters.sbd").expanduser())
    assert argv[mail_idx + 1] == str(Path("~/mail").expanduser())
    assert "~" not in argv[root_idx + 1]


def test_build_digest_argv_wraps_sticky_to_argv(tmp_path) -> None:
    from rollup.config_service import build_digest_argv, resolve_effective
    from rollup.user_config import LoadedUserConfig

    loaded = LoadedUserConfig(
        values={
            "lookback_days": 5,
            "root": str(tmp_path / "root"),
            "ollama": False,
            "no_grouping": False,
            "output": ["none"],
        },
        profiles={"deep": {"lookback_days": 5}},
    )
    eff = resolve_effective(loaded, profile_name="deep")
    argv = build_digest_argv(eff, config_path=tmp_path / "c.toml", dry_run=True)
    assert argv[0:3] == ["--config", str(tmp_path / "c.toml"), "digest"]
    assert "--profile" in argv and "deep" in argv
    lookback_idx = argv.index("--lookback-days")
    assert argv[lookback_idx + 1] == "5"
    assert "--no-ollama" in argv
    assert "--grouping" in argv
    assert "--dry-run" in argv
    # Sticky body matches sticky_to_argv for the same sticky map.
    body = sticky_to_argv(eff.sticky)
    for flag in body:
        assert flag in argv


def test_sticky_to_argv_apply_roundtrip(tmp_path) -> None:
    """Emitted argv is treated as CLI-present; bare apply still fills sticky."""
    from pathlib import Path

    sticky = {
        "lookback_days": 9,
        "root": str(tmp_path / "root"),
        "mail_root": str(tmp_path / "mail"),
        "effort": "light",
        "ollama": True,
        "ollama_model": "llama3.2",
        "no_grouping": True,
        "grouping_min_size": 4,
        "folder": ["tech"],
        "exclude_folder": ["noise"],
        "output": ["json", "txt"],
        "summary_profile": "brief",
    }
    emitted = sticky_to_argv(sticky)
    assert "--lookback-days" in emitted and "9" in emitted
    assert "--ollama" in emitted
    assert "--no-grouping" in emitted

    args = argparse.Namespace(
        lookback_days=5,
        effort=None,
        root="/default",
        mail_root="/default-mail",
        ollama=False,
        no_ollama=True,
        no_grouping=False,
        grouping=True,
        grouping_min_size=3,
        folder=None,
        exclude_folder=None,
        output=[],
        ollama_model=None,
        summary_profile=None,
    )
    apply_sticky_to_namespace(args, sticky, argv=["digest", *emitted])
    assert args.lookback_days == 5
    assert args.effort is None

    apply_sticky_to_namespace(args, sticky, argv=["digest"])
    assert args.lookback_days == 9
    assert args.effort == "light"
    assert args.ollama is True
    assert args.no_ollama is False
    assert args.no_grouping is True
    assert args.grouping is False
    assert args.folder == ["tech"]
    assert args.output == ["json", "txt"]
    assert args.summary_profile == "brief"
    assert Path(args.root) == tmp_path / "root"

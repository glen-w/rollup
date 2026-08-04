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

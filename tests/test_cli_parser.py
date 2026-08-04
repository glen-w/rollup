"""Tests for CLI parser extraction / public re-export."""

from __future__ import annotations

from rollup import cli
from rollup import cli_parser


def test_cli_reexports_build_parser() -> None:
    assert cli.build_parser is cli_parser.build_parser


def test_build_parser_digest_subcommand() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["digest", "--lookback-days", "3", "--no-ollama", "--no-grouping"]
    )
    assert args.command == "digest"
    assert args.lookback_days == 3
    assert args.no_ollama is True
    assert args.no_grouping is True

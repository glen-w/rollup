"""Declarative sticky-key ↔ CLI flag mapping (single source of truth).

Used by:
- ``user_config.apply_sticky_to_namespace`` (sticky → argparse namespace)
- ``config_service.build_digest_argv`` (sticky → ``rollup digest`` argv)

Keys in ``STICKY_KEYS`` that are not CLI-mapped must appear in
``NON_CLI_STICKY_KEYS`` (resolved outside this registry, e.g. ``profile``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from rollup.user_config import STICKY_KEYS, flag_present

Kind = Literal[
    "scalar",
    "path",
    "list",
    "bool_pair",
    "output",
]


@dataclass(frozen=True)
class StickyFlagSpec:
    """One sticky TOML key and how it maps to CLI flags / argparse attrs."""

    key: str
    kind: Kind
    # Primary flag for scalar/path, or true-branch for bool_pair.
    flag: str | None = None
    # False-branch flag for bool_pair (e.g. --no-ollama).
    false_flag: str | None = None
    # argparse attribute (defaults to key). For bool_pair: true attr.
    attr: str | None = None
    # argparse false attr for bool_pair (e.g. no_ollama).
    false_attr: str | None = None
    # Extra CLI flags that block sticky apply (output: --xteink/--x3).
    apply_block_flags: tuple[str, ...] = ()
    # Only set attr when hasattr(args, attr) (optional digest-only flags).
    require_attr: bool = False
    # Emit argv even when key absent (bool_pair defaults via sticky.get).
    always_emit_argv: bool = False
    # For scalar/list: skip argv emission when value is falsy (effort, etc.).
    argv_if_truthy: bool = False
    # bool_pair apply: when sticky value is False, only clear false_attr
    # without forcing the true attr (matches historical no_grouping apply).
    apply_false_sets_true_attr: bool = True


# profile is sticky in TOML / profiles tables but resolved via --profile /
# EffectiveConfigView.profile_name, not apply_sticky / sticky_to_argv body.
NON_CLI_STICKY_KEYS = frozenset({"profile"})

STICKY_FLAG_SPECS: tuple[StickyFlagSpec, ...] = (
    StickyFlagSpec("lookback_days", "scalar", flag="--lookback-days"),
    StickyFlagSpec("effort", "scalar", flag="--effort", argv_if_truthy=True),
    StickyFlagSpec(
        "grouping_min_size", "scalar", flag="--grouping-min-size", argv_if_truthy=True
    ),
    StickyFlagSpec(
        "ollama_model",
        "scalar",
        flag="--ollama-model",
        require_attr=True,
        argv_if_truthy=True,
    ),
    StickyFlagSpec(
        "llm_model",
        "scalar",
        flag="--llm-model",
        require_attr=True,
        argv_if_truthy=True,
    ),
    StickyFlagSpec(
        "llm_provider",
        "scalar",
        flag="--llm-provider",
        require_attr=True,
        argv_if_truthy=True,
    ),
    StickyFlagSpec(
        "summary_profile",
        "scalar",
        flag="--summary-profile",
        require_attr=True,
        argv_if_truthy=True,
    ),
    StickyFlagSpec("root", "path", flag="--root"),
    StickyFlagSpec("mail_root", "path", flag="--mail-root"),
    StickyFlagSpec("output_dir", "path", flag="--output-dir"),
    StickyFlagSpec("state_dir", "path", flag="--state-dir"),
    StickyFlagSpec("log_dir", "path", flag="--log-dir"),
    StickyFlagSpec("folder", "list", flag="--folder"),
    StickyFlagSpec("exclude_folder", "list", flag="--exclude-folder"),
    StickyFlagSpec(
        "ollama",
        "bool_pair",
        flag="--ollama",
        false_flag="--no-ollama",
        attr="ollama",
        false_attr="no_ollama",
        always_emit_argv=True,
        apply_false_sets_true_attr=True,
    ),
    StickyFlagSpec(
        "no_grouping",
        "bool_pair",
        # sticky True → --no-grouping; sticky False → --grouping
        flag="--no-grouping",
        false_flag="--grouping",
        attr="no_grouping",
        false_attr="grouping",
        always_emit_argv=True,
        # Historical apply: when no_grouping is False, only clear no_grouping.
        apply_false_sets_true_attr=False,
    ),
    StickyFlagSpec(
        "output",
        "output",
        flag="--output",
        apply_block_flags=("--xteink", "--x3"),
    ),
)


def _spec_by_key() -> dict[str, StickyFlagSpec]:
    return {s.key: s for s in STICKY_FLAG_SPECS}


def assert_sticky_keys_covered() -> None:
    """Raise if STICKY_KEYS and the registry disagree."""
    mapped = {s.key for s in STICKY_FLAG_SPECS} | NON_CLI_STICKY_KEYS
    missing = STICKY_KEYS - mapped
    extra = mapped - STICKY_KEYS
    if missing or extra:
        raise AssertionError(
            f"sticky flag registry mismatch: missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )


def apply_sticky_specs(
    args: Any,
    sticky: Mapping[str, Any],
    argv: list[str],
) -> None:
    """Apply sticky config onto argparse namespace where CLI did not set the flag."""
    for spec in STICKY_FLAG_SPECS:
        if spec.key not in sticky:
            continue
        value = sticky[spec.key]
        if spec.kind == "scalar":
            assert spec.flag is not None
            if flag_present(argv, spec.flag):
                continue
            attr = spec.attr or spec.key
            if spec.require_attr and not hasattr(args, attr):
                continue
            setattr(args, attr, value)
        elif spec.kind == "path":
            assert spec.flag is not None
            if flag_present(argv, spec.flag):
                continue
            setattr(args, spec.attr or spec.key, value)
        elif spec.kind == "list":
            assert spec.flag is not None
            if flag_present(argv, spec.flag):
                continue
            setattr(args, spec.attr or spec.key, list(value))
        elif spec.kind == "bool_pair":
            assert spec.flag is not None and spec.false_flag is not None
            assert spec.attr is not None and spec.false_attr is not None
            if flag_present(argv, spec.flag) or flag_present(argv, spec.false_flag):
                continue
            if value:
                setattr(args, spec.attr, True)
                setattr(args, spec.false_attr, False)
            else:
                setattr(args, spec.attr, False)
                if spec.apply_false_sets_true_attr:
                    setattr(args, spec.false_attr, True)
        elif spec.kind == "output":
            assert spec.flag is not None
            blocked = flag_present(argv, spec.flag) or any(
                flag_present(argv, f) for f in spec.apply_block_flags
            )
            if blocked:
                continue
            values = list(value)
            if values == ["all"]:
                # Empty list → default-all policy in requested_writer_names.
                args.output = []
            else:
                args.output = values


def sticky_to_argv(sticky: Mapping[str, Any]) -> list[str]:
    """Emit digest CLI flags for sticky values (Run Studio / display)."""
    argv: list[str] = []
    specs = _spec_by_key()

    # Stable emission order matching historical build_digest_argv.
    order = (
        "lookback_days",
        "root",
        "mail_root",
        "output_dir",
        "state_dir",
        "log_dir",
        "folder",
        "exclude_folder",
        "effort",
        "ollama",
        "ollama_model",
        "llm_provider",
        "llm_model",
        "summary_profile",
        "no_grouping",
        "grouping_min_size",
        "output",
    )
    for key in order:
        spec = specs[key]
        if spec.kind == "bool_pair" and spec.always_emit_argv:
            assert spec.flag is not None and spec.false_flag is not None
            # sticky True → primary flag; False/absent → false_flag.
            if sticky.get(key):
                argv.append(spec.flag)
            else:
                argv.append(spec.false_flag)
            continue
        if key not in sticky:
            continue
        value = sticky[key]
        if spec.kind == "scalar":
            assert spec.flag is not None
            if spec.argv_if_truthy and not value:
                continue
            argv.extend([spec.flag, str(value)])
        elif spec.kind == "path":
            assert spec.flag is not None
            if not value:
                continue
            argv.extend([spec.flag, str(Path(str(value)).expanduser())])
        elif spec.kind == "list":
            assert spec.flag is not None
            for item in value or []:
                argv.extend([spec.flag, item])
        elif spec.kind == "output":
            assert spec.flag is not None
            outputs = list(value or [])
            if outputs == ["all"] or not outputs:
                pass  # default-all
            elif outputs == ["none"]:
                argv.extend([spec.flag, "none"])
            else:
                for name in outputs:
                    argv.extend([spec.flag, name])
    return argv


assert_sticky_keys_covered()

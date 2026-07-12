"""CLI handlers for ``rollup sources`` subcommands."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rollup.config import DEFAULT_STATE_DIR
from rollup.run_lock import RunLockError, acquire_state_lock
from rollup.safety import SafetyError, assert_safe_write_paths
from rollup.source_io import export_sources, import_sources
from rollup.source_registry import (
    AmbiguousSourceRef,
    SourceNotFound,
    SourceRegistryError,
    alias_sources,
    clear_overrides,
    ensure_registry,
    ensure_source_anchor,
    get_source_record,
    list_source_keys,
    resolve_source_ref,
    set_overrides,
)
from rollup.state import init_db


SOURCES_JSON_SCHEMA_VERSION = 1


def _state_dir(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "state_dir", DEFAULT_STATE_DIR))


def _open_db(args: argparse.Namespace):
    state_dir = _state_dir(args)
    db_path = state_dir / "rollup.db"
    conn = init_db(db_path)
    ensure_registry(conn)
    return conn, state_dir


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_sources(args: argparse.Namespace) -> int:
    command = getattr(args, "sources_command", None)
    handlers = {
        "list": _cmd_list,
        "show": _cmd_show,
        "set": _cmd_set,
        "clear": _cmd_clear,
        "enable": _cmd_enable,
        "disable": _cmd_disable,
        "alias": _cmd_alias,
        "export": _cmd_export,
        "import": _cmd_import,
        "doctor": _cmd_doctor,
    }
    handler = handlers.get(command)
    if handler is None:
        print(f"Unknown sources command: {command}", file=sys.stderr)
        return 1
    try:
        return handler(args)
    except AmbiguousSourceRef as exc:
        print(str(exc), file=sys.stderr)
        for c in exc.candidates:
            print(f"  {c}", file=sys.stderr)
        return 1
    except (SourceNotFound, SourceRegistryError, SafetyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RunLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _cmd_list(args: argparse.Namespace) -> int:
    conn, _ = _open_db(args)
    try:
        keys = list_source_keys(conn)
        rows = []
        for key in keys:
            rec = get_source_record(conn, key)
            rows.append(
                {
                    "source_key": rec.source_key,
                    "enabled": rec.policy.enabled,
                    "always_surface": rec.policy.always_surface,
                    "priority": rec.policy.priority,
                    "grouping_policy": rec.policy.grouping_policy,
                    "newsletter_type_override": rec.policy.newsletter_type_override,
                    "last_seen_at": rec.observation.last_seen_at,
                    "message_count_total": rec.observation.message_count_total,
                    "display_name": rec.policy.display_name_override
                    or rec.policy.display_name_observed,
                }
            )
        if getattr(args, "json", False):
            _print_json({"schema_version": SOURCES_JSON_SCHEMA_VERSION, "sources": rows})
        else:
            if not rows:
                print("No sources registered.")
            for row in rows:
                flag = "" if row["enabled"] else " [disabled]"
                print(
                    f"{row['source_key']}{flag}  "
                    f"priority={row['priority']}  "
                    f"grouping={row['grouping_policy']}  "
                    f"seen={row['last_seen_at'] or '-'}  "
                    f"count={row['message_count_total']}"
                )
        return 0
    finally:
        conn.close()


def _cmd_show(args: argparse.Namespace) -> int:
    conn, _ = _open_db(args)
    try:
        key = resolve_source_ref(conn, args.source)
        rec = get_source_record(conn, key)
        payload = {
            "schema_version": SOURCES_JSON_SCHEMA_VERSION,
            "source_key": rec.source_key,
            "lifecycle": rec.lifecycle,
            "superseded_by": rec.superseded_by,
            "policy": asdict(rec.policy),
            "overrides": asdict(rec.overrides),
            "observation": {
                **{k: v for k, v in asdict(rec.observation).items() if k != "cadence"},
                "cadence": asdict(rec.observation.cadence),
            },
        }
        if getattr(args, "json", False):
            _print_json(payload)
        else:
            print(f"source_key: {rec.source_key}")
            print(f"lifecycle: {rec.lifecycle}")
            print(f"enabled: {rec.policy.enabled}")
            print(f"always_surface: {rec.policy.always_surface}")
            print(f"priority: {rec.policy.priority}")
            print(f"grouping_policy: {rec.policy.grouping_policy}")
            print(f"newsletter_type_override: {rec.policy.newsletter_type_override}")
            print(f"summary_profile_override: {rec.policy.summary_profile_override}")
            print(f"display_name: {rec.policy.display_name_override or rec.policy.display_name_observed}")
            print(f"notes: {rec.overrides.notes}")
            print(f"message_count_total: {rec.observation.message_count_total}")
            print(f"cadence: {rec.observation.cadence.label} (n={rec.observation.cadence.sample_count})")
            if rec.policy.corrupt_fields:
                print(f"corrupt_fields: {', '.join(rec.policy.corrupt_fields)}")
        return 0
    finally:
        conn.close()


def _with_write_lock(args: argparse.Namespace, operation: str, fn) -> int:
    state_dir = _state_dir(args)
    dry_run = bool(getattr(args, "dry_run", False))
    if dry_run:
        conn, _ = _open_db(args)
        try:
            return fn(conn, dry_run=True)
        finally:
            conn.close()
    lock = acquire_state_lock(
        state_dir, run_id=str(uuid.uuid4()), operation=operation
    )
    try:
        conn, _ = _open_db(args)
        try:
            return fn(conn, dry_run=False)
        finally:
            conn.close()
    finally:
        lock.release()


def _cmd_set(args: argparse.Namespace) -> int:
    def run(conn, *, dry_run: bool) -> int:
        key = resolve_source_ref(conn, args.source)
        updates: dict[str, Any] = {}
        if getattr(args, "enabled", None) is not None:
            updates["enabled"] = bool(args.enabled)
        if getattr(args, "disabled", False):
            updates["enabled"] = False
        if getattr(args, "always_surface", None) is not None:
            updates["always_surface"] = bool(args.always_surface)
        if getattr(args, "no_always_surface", False):
            updates["always_surface"] = False
        if getattr(args, "priority", None) is not None:
            updates["priority"] = int(args.priority)
        if getattr(args, "type", None) is not None:
            updates["newsletter_type"] = args.type
        if getattr(args, "grouping", None) is not None:
            updates["grouping_policy"] = args.grouping
        if getattr(args, "summary_profile", None) is not None:
            updates["summary_profile"] = args.summary_profile
        if getattr(args, "cadence", None) is not None:
            updates["expected_cadence"] = args.cadence
        if getattr(args, "display_name", None) is not None:
            updates["display_name"] = args.display_name
        if getattr(args, "notes", None) is not None:
            updates["notes"] = args.notes
        if not updates:
            print("ERROR: no fields to set", file=sys.stderr)
            return 1
        if dry_run:
            print(f"dry-run: would set {key} {updates}")
            return 0
        set_overrides(conn, key, updates=updates, updated_by="cli")
        print(f"Updated {key}")
        return 0

    return _with_write_lock(args, "sources_set", run)


def _cmd_clear(args: argparse.Namespace) -> int:
    def run(conn, *, dry_run: bool) -> int:
        key = resolve_source_ref(conn, args.source)
        fields = None
        if getattr(args, "all", False):
            fields = None
        else:
            fields = []
            for name in (
                "enabled",
                "always_surface",
                "priority",
                "newsletter_type",
                "grouping_policy",
                "summary_profile",
                "expected_cadence",
                "display_name",
                "notes",
            ):
                if getattr(args, f"clear_{name}", False) or getattr(
                    args, name, False
                ):
                    fields.append(name)
            if not fields:
                fields = None
        if dry_run:
            print(f"dry-run: would clear {key} fields={fields or 'all'}")
            return 0
        clear_overrides(conn, key, fields=fields, updated_by="cli")
        print(f"Cleared overrides on {key}")
        return 0

    return _with_write_lock(args, "sources_clear", run)


def _cmd_enable(args: argparse.Namespace) -> int:
    args.enabled = True
    return _cmd_set(args)


def _cmd_disable(args: argparse.Namespace) -> int:
    args.enabled = False
    return _cmd_set(args)


def _cmd_alias(args: argparse.Namespace) -> int:
    def run(conn, *, dry_run: bool) -> int:
        alias = resolve_source_ref(conn, args.alias) if False else args.alias
        # Alias key may be a raw key not yet in DB.
        try:
            canonical = resolve_source_ref(conn, args.canonical)
        except SourceNotFound:
            canonical = args.canonical
        if dry_run:
            print(f"dry-run: would alias {alias} -> {canonical}")
            return 0
        ensure_source_anchor(conn, canonical)
        alias_sources(conn, alias, canonical, note=getattr(args, "note", None))
        print(f"Aliased {alias} -> {canonical}")
        return 0

    return _with_write_lock(args, "sources_alias", run)


def _cmd_export(args: argparse.Namespace) -> int:
    conn, state_dir = _open_db(args)
    try:
        out = Path(args.out)
        mail_root = Path(getattr(args, "mail_root", "/tmp"))
        # Use state_dir parent as mail_root stand-in when not provided carefully
        from rollup.config import DEFAULT_MAIL_ROOT

        mail_root = Path(getattr(args, "mail_root", DEFAULT_MAIL_ROOT))
        assert_safe_write_paths(mail_root, [out, state_dir])
        export_sources(
            conn,
            out,
            include_observations=bool(getattr(args, "include_observations", False)),
        )
        print(f"Exported sources to {out}")
        return 0
    finally:
        conn.close()


def _cmd_import(args: argparse.Namespace) -> int:
    def run(conn, *, dry_run: bool) -> int:
        path = Path(args.from_path)
        replace_all = bool(getattr(args, "replace_all", False))
        if replace_all and not getattr(args, "i_understand_replace_all", False):
            print(
                "ERROR: --replace-all requires --i-understand-replace-all",
                file=sys.stderr,
            )
            return 1
        result = import_sources(
            conn,
            path,
            merge=not replace_all,
            dry_run=dry_run,
            replace_all=replace_all,
        )
        print(
            f"{'dry-run ' if dry_run else ''}import: "
            f"create={result.created} update={result.updated} "
            f"clear={result.cleared} delete={result.deleted}"
        )
        return 0

    return _with_write_lock(args, "sources_import", run)


def _cmd_doctor(args: argparse.Namespace) -> int:
    from rollup.source_doctor import run_source_doctor

    conn, _ = _open_db(args)
    try:
        report = run_source_doctor(conn)
        if getattr(args, "json", False):
            _print_json(report)
        else:
            for check in report["checks"]:
                print(f"[{check['status']}] {check['id']}: {check['message']}")
        return 0 if report.get("ok") else 1
    finally:
        conn.close()


def add_sources_subparser(sub) -> None:
    sources = sub.add_parser("sources", help="Manage persistent newsletter sources")
    src_sub = sources.add_subparsers(dest="sources_command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
        p.add_argument("--mail-root", default=None)
        p.add_argument("--verbose", action="store_true", default=False)
        p.add_argument("--quiet", action="store_true", default=False)

    p_list = src_sub.add_parser("list", help="List known sources")
    common(p_list)
    p_list.add_argument("--json", action="store_true")

    p_show = src_sub.add_parser("show", help="Show one source")
    common(p_show)
    p_show.add_argument("source")
    p_show.add_argument("--json", action="store_true")

    p_set = src_sub.add_parser("set", help="Set source overrides")
    common(p_set)
    p_set.add_argument("source")
    p_set.add_argument("--dry-run", action="store_true")
    p_set.add_argument("--enabled", action="store_true", default=None)
    p_set.add_argument("--disabled", action="store_true", default=False)
    p_set.add_argument("--always-surface", dest="always_surface", action="store_true", default=None)
    p_set.add_argument("--no-always-surface", action="store_true", default=False)
    p_set.add_argument("--priority", type=int)
    p_set.add_argument("--type", dest="type")
    p_set.add_argument("--grouping")
    p_set.add_argument("--summary-profile")
    p_set.add_argument("--cadence")
    p_set.add_argument("--display-name")
    p_set.add_argument("--notes")

    p_clear = src_sub.add_parser("clear", help="Clear source overrides")
    common(p_clear)
    p_clear.add_argument("source")
    p_clear.add_argument("--dry-run", action="store_true")
    p_clear.add_argument("--all", action="store_true")

    for name in ("enable", "disable"):
        p = src_sub.add_parser(name, help=f"{name.capitalize()} a source")
        common(p)
        p.add_argument("source")
        p.add_argument("--dry-run", action="store_true")

    p_alias = src_sub.add_parser("alias", help="Alias a source key to a canonical key")
    common(p_alias)
    p_alias.add_argument("alias")
    p_alias.add_argument("canonical")
    p_alias.add_argument("--note")
    p_alias.add_argument("--dry-run", action="store_true")

    p_export = src_sub.add_parser("export", help="Export overrides and aliases")
    common(p_export)
    p_export.add_argument("--out", required=True)
    p_export.add_argument("--include-observations", action="store_true")

    p_import = src_sub.add_parser("import", help="Import overrides and aliases")
    common(p_import)
    p_import.add_argument("--from", dest="from_path", required=True)
    p_import.add_argument("--replace-all", action="store_true")
    p_import.add_argument("--i-understand-replace-all", action="store_true")
    p_import.add_argument("--dry-run", action="store_true")

    p_doc = src_sub.add_parser("doctor", help="Check source registry health")
    common(p_doc)
    p_doc.add_argument("--json", action="store_true")

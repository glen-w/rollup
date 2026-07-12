"""CLI for reader body maintenance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rollup.config import DEFAULT_MAIL_ROOT, DEFAULT_STATE_DIR
from rollup.reader_body_admin import collect_stats, require_schema, run_check
from rollup.reader_body_backfill import (
    BackfillScope,
    delete_all_bodies,
    prune_orphans,
    run_backfill,
)
from rollup.state import connect_db, get_schema_version, init_db


def _db(state_dir: Path, migrate: bool):
    path = state_dir / "rollup.db"
    if migrate:
        return init_db(path)
    return connect_db(path)


def cmd_stats(args: argparse.Namespace) -> int:
    conn = _db(Path(args.state_dir), migrate=False)
    try:
        require_schema(conn)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    stats = collect_stats(conn, db_path=Path(args.state_dir) / "rollup.db")
    if args.json:
        print(
            json.dumps(
                {
                    "total_rows": stats.total_rows,
                    "populated": stats.populated,
                    "empty": stats.empty,
                    "truncated": stats.truncated,
                    "orphans": stats.orphans,
                    "coverage_pct": stats.coverage_pct,
                    "coverage_numerator": stats.coverage_numerator,
                    "coverage_denominator": stats.coverage_denominator,
                    "db_file_bytes": stats.db_file_bytes,
                    "table_storage": stats.table_storage,
                }
            )
        )
    else:
        print(f"Reader bodies: {stats.total_rows} (orphans {stats.orphans})")
    conn.close()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    conn = _db(Path(args.state_dir), migrate=False)
    try:
        require_schema(conn)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    report = run_check(conn)
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": report.schema_version,
                    "issues": [{"code": i.code, "count": i.count} for i in report.issues],
                }
            )
        )
    else:
        for issue in report.issues:
            print(f"{issue.code}: {issue.count}")
    conn.close()
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    conn = init_db(Path(args.state_dir) / "rollup.db")
    scope = BackfillScope(
        retained_entries_only=not args.all,
        run_id=args.run,
        source_key=args.source,
    )
    result = run_backfill(
        conn,
        mail_root=Path(args.root or DEFAULT_MAIL_ROOT),
        scope=scope,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result.__dict__))
    else:
        print(
            f"backfill: candidates={result.candidates} matched={result.matched} "
            f"inserted={result.inserted} updated={result.updated}"
        )
    conn.close()
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    if not args.yes and not args.dry_run:
        print("Refusing without --yes or --dry-run", file=sys.stderr)
        return 1
    conn = init_db(Path(args.state_dir) / "rollup.db")
    n = prune_orphans(conn, dry_run=args.dry_run)
    print(f"orphans: {n}")
    conn.close()
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    if not args.yes and not args.dry_run:
        print("Refusing delete without --yes or --dry-run", file=sys.stderr)
        return 1
    conn = init_db(Path(args.state_dir) / "rollup.db")
    n = delete_all_bodies(conn, dry_run=args.dry_run)
    print(f"deleted: {n}")
    conn.close()
    return 0


def cmd_vacuum(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing vacuum without --yes", file=sys.stderr)
        return 1
    path = Path(args.state_dir) / "rollup.db"
    conn = connect_db(path)
    conn.execute("VACUUM")
    conn.close()
    return 0


def add_bodies_subparser(sub) -> None:
    bodies = sub.add_parser("bodies", help="Reader body maintenance")
    bodies_sub = bodies.add_subparsers(dest="bodies_command", required=True)

    p_stats = bodies_sub.add_parser("stats", help="Aggregate reader body stats")
    p_stats.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=cmd_stats)

    p_check = bodies_sub.add_parser("check", help="Integrity check report")
    p_check.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=cmd_check)

    p_bf = bodies_sub.add_parser("backfill", help="Backfill missing bodies from mbox")
    p_bf.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p_bf.add_argument("--root", default=None)
    p_bf.add_argument("--run", default=None)
    p_bf.add_argument("--source", default=None)
    p_bf.add_argument("--all", action="store_true")
    p_bf.add_argument("--dry-run", action="store_true")
    p_bf.add_argument("--json", action="store_true")
    p_bf.set_defaults(func=cmd_backfill)

    p_prune = bodies_sub.add_parser("prune", help="Remove orphan bodies")
    p_prune.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p_prune.add_argument("--dry-run", action="store_true")
    p_prune.add_argument("--yes", action="store_true")
    p_prune.set_defaults(func=cmd_prune)

    p_del = bodies_sub.add_parser("delete", help="Delete all reader bodies")
    p_del.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p_del.add_argument("--dry-run", action="store_true")
    p_del.add_argument("--yes", action="store_true")
    p_del.set_defaults(func=cmd_delete)

    p_vac = bodies_sub.add_parser("vacuum", help="Vacuum database after deletion")
    p_vac.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p_vac.add_argument("--yes", action="store_true")
    p_vac.set_defaults(func=cmd_vacuum)


def cmd_bodies(args: argparse.Namespace) -> int:
    return args.func(args)

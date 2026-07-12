"""CLI handlers for rollup web (Flask optional)."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from rollup.config import DEFAULT_MAIL_ROOT, DEFAULT_OUTPUT_DIR, DEFAULT_STATE_DIR
from rollup.safety import SafetyError, assert_safe_write_paths
from rollup.web.bind import BindError, validate_bind_host


def _ensure_flask() -> str | None:
    """Return an error message if Flask is missing; otherwise None."""
    try:
        import flask  # noqa: F401
    except ImportError:
        return (
            "Flask is required for rollup web.\n"
            "Install with: pip install 'rollup[web]'"
        )
    return None


def cmd_web(args: argparse.Namespace) -> int:
    if getattr(args, "web_command", None) == "reindex":
        return cmd_web_reindex(args)

    missing = _ensure_flask()
    if missing:
        print(missing, file=sys.stderr)
        return 1
    from rollup.web.app import create_app

    state_dir = Path(args.state_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    mail_root = Path(args.mail_root).expanduser()
    log_dir = Path(getattr(args, "log_dir", "./logs")).expanduser()

    try:
        assert_safe_write_paths(mail_root, state_dir, output_dir, log_dir)
        host = validate_bind_host(args.host)
    except (SafetyError, BindError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    app = create_app(
        state_dir=state_dir,
        output_dir=output_dir,
        mail_root=mail_root,
    )
    url = f"http://{host}:{args.port}/"
    print(f"Rollup web listening on {url} (loopback only)", file=sys.stderr)
    if args.open:
        webbrowser.open(url)
    # Local development server only — Ctrl-C stops the process.
    app.run(
        host=host,
        port=args.port,
        debug=bool(args.debug),
        use_reloader=bool(args.debug),
    )
    return 0


def cmd_web_reindex(args: argparse.Namespace) -> int:
    from rollup.run_index import reindex_from_manifests
    from rollup.state import init_db

    state_dir = Path(args.state_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    mail_root = Path(args.mail_root).expanduser()
    try:
        assert_safe_write_paths(mail_root, state_dir, output_dir)
    except SafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    db_path = state_dir / "rollup.db"
    init_db(db_path).close()
    n = reindex_from_manifests(db_path, state_dir, output_dir)
    print(f"Backfilled {n} run(s) from manifests")
    return 0


def register_web_parser(sub: argparse._SubParsersAction) -> None:
    web = sub.add_parser(
        "web",
        help="Local loopback web UI for browsing rollups and ratings",
    )
    web_sub = web.add_subparsers(dest="web_command", required=False)
    web.add_argument("--host", default="127.0.0.1", help="Bind host (loopback only)")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    web.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    web.add_argument("--mail-root", default=str(DEFAULT_MAIL_ROOT))
    web.add_argument("--log-dir", default="./logs")
    web.add_argument("--open", action="store_true", help="Open browser once")
    web.add_argument("--debug", action="store_true", help="Flask debug (loopback only)")
    web.set_defaults(func_web=cmd_web)

    reindex = web_sub.add_parser(
        "reindex", help="Backfill rollup_runs metadata from manifests"
    )
    reindex.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    reindex.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    reindex.add_argument("--mail-root", default=str(DEFAULT_MAIL_ROOT))
    reindex.set_defaults(func_web=cmd_web_reindex)

"""CLI handlers for ``rollup gmail auth|labels|status``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rollup.config import DEFAULT_STATE_DIR
from rollup.error_sanitize import sanitize_provider_message
from rollup.gmail.config import GmailConfig, folder_name_for_label
from rollup.gmail.session import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    GmailSessionError,
    build_gmail_client,
    credentials_configured,
    read_token_file,
    token_configured,
    token_path_for,
    token_scope_is_readonly,
)


def _state_dir(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "state_dir", DEFAULT_STATE_DIR)).expanduser()


def _gmail_config(args: argparse.Namespace) -> GmailConfig:
    loaded = getattr(args, "_loaded_user_config", None)
    return getattr(loaded, "gmail", None) or GmailConfig()


def _err(message: str) -> None:
    print(f"ERROR: {sanitize_provider_message(message)}", file=sys.stderr)


def cmd_gmail_auth(args: argparse.Namespace) -> int:
    from rollup.gmail.auth import run_loopback_auth

    state_dir = _state_dir(args)
    token_path = token_path_for(state_dir)
    try:
        run_loopback_auth(
            token_path=token_path,
            open_browser=not getattr(args, "no_browser", False),
            manual=getattr(args, "manual", False),
        )
    except GmailSessionError as exc:
        _err(str(exc))
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.", file=sys.stderr)
        return 1
    print(f"Gmail authorised (gmail.readonly). Token saved to {token_path}")
    return 0


def cmd_gmail_labels(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    cfg = _gmail_config(args)
    client = build_gmail_client(state_dir)
    try:
        labels = client.list_labels()
    except GmailSessionError as exc:
        _err(str(exc))
        return 1
    strip = cfg.effective_strip_prefix
    configured = set(cfg.labels)
    prefix = cfg.label_prefix
    rows = []
    for name in sorted(labels):
        selected = name in configured or bool(
            prefix and (name == prefix or name.startswith(prefix + "/"))
        )
        rows.append(
            {
                "name": name,
                "id": labels[name],
                "selected": selected,
                "folder": folder_name_for_label(name, strip_prefix=strip) if selected else None,
            }
        )
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        mark = "*" if row["selected"] else " "
        folder = f"  → {row['folder']}" if row["folder"] else ""
        print(f"{mark} {row['name']}{folder}")
    if not any(r["selected"] for r in rows):
        print("\n(no labels selected; set [gmail].labels or [gmail].label_prefix in config.toml)")
    return 0


def gmail_status(state_dir: Path, cfg: GmailConfig, *, now: datetime | None = None) -> dict:
    """Offline status summary (used by the CLI, doctor, and Settings)."""
    from rollup.source_fetch_cache import format_cache_age

    when = now or datetime.now(timezone.utc)
    token_path = token_path_for(state_dir)
    status: dict = {
        "enabled": cfg.enabled,
        "configured": cfg.configured,
        "labels": list(cfg.labels),
        "label_prefix": cfg.label_prefix,
        "credentials_env": credentials_configured(),
        "credentials_env_names": [CLIENT_ID_ENV, CLIENT_SECRET_ENV],
        "token_path": str(token_path),
        "token_present": token_configured(state_dir),
        "token_scope_readonly": None,
        "cached_labels": [],
        "cached_messages": 0,
    }
    if status["token_present"]:
        try:
            data = read_token_file(token_path)
            status["token_scope_readonly"] = token_scope_is_readonly(data)
        except GmailSessionError as exc:
            status["token_error"] = str(exc)
    db_path = state_dir / "rollup.db"
    if db_path.is_file():
        from rollup.gmail.cache import count_cached_messages, list_label_snapshots
        from rollup.state import init_db

        conn = init_db(db_path)
        try:
            status["cached_messages"] = count_cached_messages(conn)
            status["cached_labels"] = [
                {
                    "label": snap.label_name,
                    "messages": len(snap.message_ids),
                    "age": format_cache_age(snap.fetched_at, when),
                }
                for snap in list_label_snapshots(conn)
            ]
        finally:
            conn.close()
    return status


def cmd_gmail_status(args: argparse.Namespace) -> int:
    status = gmail_status(_state_dir(args), _gmail_config(args))
    if getattr(args, "json", False):
        print(json.dumps(status, indent=2))
        return 0
    print(f"enabled:      {'yes' if status['enabled'] else 'no'}")
    if status["label_prefix"]:
        print(f"label_prefix: {status['label_prefix']}")
    if status["labels"]:
        print(f"labels:       {', '.join(status['labels'])}")
    creds = "set" if status["credentials_env"] else f"missing ({', '.join(status['credentials_env_names'])})"
    print(f"credentials:  {creds}")
    token = "present" if status["token_present"] else "missing (run `rollup gmail auth`)"
    if status.get("token_error"):
        token = f"invalid: {status['token_error']}"
    elif status["token_present"] and status["token_scope_readonly"] is False:
        token = "present but scope is not gmail.readonly (re-run `rollup gmail auth`)"
    print(f"token:        {token} [{status['token_path']}]")
    print(f"cached msgs:  {status['cached_messages']}")
    for row in status["cached_labels"]:
        print(f"  {row['label']}: {row['messages']} msgs, synced {row['age']}")
    return 0


def cmd_gmail(args: argparse.Namespace) -> int:
    sub = getattr(args, "gmail_command", None)
    if sub == "auth":
        return cmd_gmail_auth(args)
    if sub == "labels":
        return cmd_gmail_labels(args)
    if sub == "status":
        return cmd_gmail_status(args)
    print("usage: rollup gmail {auth,labels,status}", file=sys.stderr)
    return 2

"""Session secret file handling for the local web UI."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from rollup.fsutil import atomic_write_bytes


class WebSecretError(RuntimeError):
    pass


def load_or_create_secret(state_dir: Path) -> bytes:
    path = Path(state_dir) / "web_secret"
    if path.is_symlink():
        raise WebSecretError(f"refusing symlink secret file: {path}")
    if path.exists():
        if not path.is_file():
            raise WebSecretError(f"secret path is not a file: {path}")
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise WebSecretError(f"secret file permissions too open: {path}")
        data = path.read_bytes().strip()
        if len(data) < 16:
            raise WebSecretError("secret file too short or empty")
        return data
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, secret)
    os.chmod(path, 0o600)
    return secret

"""Session secret and extension capture-token file handling for the local web UI."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from rollup.fsutil import atomic_write_bytes, atomic_write_text

EXTENSION_TOKEN_FILENAME = "extension_token"


class WebSecretError(RuntimeError):
    pass


def _assert_secret_path(path: Path) -> None:
    if path.is_symlink():
        raise WebSecretError(f"refusing symlink secret file: {path}")
    if path.exists() and not path.is_file():
        raise WebSecretError(f"secret path is not a file: {path}")


def _assert_secret_mode(path: Path) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise WebSecretError(f"secret file permissions too open: {path}")


def load_or_create_secret(state_dir: Path) -> bytes:
    path = Path(state_dir) / "web_secret"
    _assert_secret_path(path)
    if path.exists():
        _assert_secret_mode(path)
        # Binary secret — never strip(); whitespace bytes are valid in token_bytes.
        data = path.read_bytes()
        if len(data) < 16:
            raise WebSecretError("secret file too short or empty")
        return data
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, secret)
    os.chmod(path, 0o600)
    return secret


def _write_extension_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, token + "\n")
    os.chmod(path, 0o600)


def load_or_create_extension_token(state_dir: Path) -> str:
    """Load or create `{state_dir}/extension_token` (urlsafe text, mode 0600)."""
    path = Path(state_dir) / EXTENSION_TOKEN_FILENAME
    _assert_secret_path(path)
    if path.exists():
        _assert_secret_mode(path)
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 16:
            raise WebSecretError("extension token file too short or empty")
        return token
    token = secrets.token_urlsafe(32)
    _write_extension_token(path, token)
    return token


def rotate_extension_token(state_dir: Path) -> str:
    """Replace the capture token. The previous value is invalid immediately."""
    path = Path(state_dir) / EXTENSION_TOKEN_FILENAME
    _assert_secret_path(path)
    token = secrets.token_urlsafe(32)
    _write_extension_token(path, token)
    return token

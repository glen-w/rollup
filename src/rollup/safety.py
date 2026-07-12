"""Read-only safety guards for mail store and writable paths."""

from __future__ import annotations

import os
from pathlib import Path


class SafetyError(Exception):
    """Raised when a path would violate read-only mail guarantees."""


def is_inside(child: Path, parent: Path) -> bool:
    """Return True if resolved child is inside or equal to resolved parent."""
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if child_resolved == parent_resolved:
        return True
    try:
        return os.path.commonpath([str(child_resolved), str(parent_resolved)]) == str(
            parent_resolved
        )
    except ValueError:
        return False


def assert_safe_write_paths(mail_root: Path, *paths: Path) -> None:
    """Reject any writable path that resolves inside the mail root."""
    mail_root = mail_root.resolve()
    for path in paths:
        resolved = path.resolve()
        if is_inside(resolved, mail_root):
            raise SafetyError(
                f"Refusing to write to {path}: resolves inside mail root {mail_root}"
            )


def validate_read_root(
    root: Path,
    mail_root: Path,
    output_dir: Path,
    state_dir: Path,
    log_dir: Path,
) -> list[str]:
    """Validate read root; return warning messages."""
    warnings: list[str] = []
    root = root.resolve()
    if not root.exists():
        raise SafetyError(f"Newsletter root does not exist: {root}")
    if not root.is_dir():
        raise SafetyError(f"Newsletter root is not a directory: {root}")

    for label, path in [
        ("output_dir", output_dir),
        ("state_dir", state_dir),
        ("log_dir", log_dir),
    ]:
        if root.resolve() == path.resolve():
            raise SafetyError(f"--root must not equal {label}")

    live_newsletters = (mail_root / "Newsletters.sbd").resolve()
    fixture_hint = Path("tests/fixtures/Newsletters.sbd").resolve()
    if root == live_newsletters and root != fixture_hint:
        warnings.append(
            "WARNING: Reading live Thunderbird data. Recommend testing with:\n"
            "  python -m rollup inventory --root tests/fixtures/Newsletters.sbd\n"
            "Before copying real mail, confirm .gitignore contains fixtures/.\n"
            "Never commit files copied from your live mail root "
            f"({mail_root})."
        )
    return warnings

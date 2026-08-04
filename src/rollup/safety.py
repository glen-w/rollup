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


def nearest_existing_parent(path: Path) -> Path:
    """Return the nearest existing ancestor of path (or path if it exists)."""
    current = path.expanduser()
    # Do not resolve yet — walk parents until one exists, then resolve that.
    while True:
        try:
            if current.exists():
                return current.resolve()
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            return current.resolve()
        current = parent


def assert_safe_write_paths(mail_root: Path, *paths: Path) -> None:
    """Reject any writable path that resolves inside the mail root."""
    mail_root = mail_root.resolve()
    for path in paths:
        resolved = path.resolve()
        if is_inside(resolved, mail_root):
            raise SafetyError(
                f"Refusing to write to {path}: resolves inside mail root {mail_root}"
            )


def assert_safe_write_paths_dual(
    newsletter_root: Path,
    mail_root: Path,
    *paths: Path,
) -> None:
    """Reject writes inside newsletter root or verified mail root.

    For non-existent destinations, containment is checked via the nearest
    existing parent so output files and SQLite sidecars are covered before create.
    """
    newsletter_root = newsletter_root.resolve()
    mail_root = mail_root.resolve()
    if not is_inside(newsletter_root, mail_root):
        raise SafetyError(
            f"Newsletter root {newsletter_root} is not inside mail root {mail_root}"
        )

    protected = (newsletter_root, mail_root)
    for path in paths:
        expanded = path.expanduser()
        # Symlink alias of a protected root: reject if any path component is a
        # symlink that resolves inside a protected tree, or final resolve is inside.
        check_targets = [nearest_existing_parent(expanded)]
        try:
            if expanded.exists() or expanded.parent.exists():
                check_targets.append(expanded.resolve())
        except OSError as exc:
            raise SafetyError(f"Cannot resolve writable path {path}: {exc}") from exc

        # Walk lexical parents for symlink escape before create.
        cursor = expanded
        for _ in range(64):
            if cursor.is_symlink():
                try:
                    check_targets.append(cursor.resolve())
                except OSError as exc:
                    raise SafetyError(
                        f"Cannot resolve symlink writable path {path}: {exc}"
                    ) from exc
            parent = cursor.parent
            if parent == cursor:
                break
            cursor = parent

        for target in check_targets:
            for zone in protected:
                if is_inside(target, zone):
                    raise SafetyError(
                        f"Refusing to write to {path}: resolves inside protected "
                        f"root {zone}"
                    )


def validate_mail_root_relationship(newsletter_root: Path, mail_root: Path) -> None:
    """Require newsletter root to be contained in (or equal to) mail root."""
    newsletter_root = newsletter_root.resolve()
    mail_root = mail_root.resolve()
    if not is_inside(newsletter_root, mail_root):
        raise SafetyError(
            f"Newsletter root {newsletter_root} must be inside mail root {mail_root}. "
            "Pass an explicit --mail-root when inference is wrong."
        )


def collect_writable_run_paths(
    *,
    output_dir: Path,
    state_dir: Path,
    log_dir: Path,
    db_path: Path | None = None,
    extra: tuple[Path, ...] = (),
) -> tuple[Path, ...]:
    """Paths that may be created or written during a digest/admin run."""
    db = db_path if db_path is not None else state_dir / "rollup.db"
    paths: list[Path] = [
        output_dir,
        state_dir,
        log_dir,
        db,
        Path(str(db) + "-wal"),
        Path(str(db) + "-shm"),
        Path(str(db) + "-journal"),
        state_dir / "manifests",
        state_dir / "rollup.lock",
        output_dir / "archive",
        output_dir / "latest.md",
        output_dir / "latest.html",
        *extra,
    ]
    return tuple(paths)


def validate_writable_run_paths(
    *,
    newsletter_root: Path,
    mail_root: Path,
    output_dir: Path,
    state_dir: Path,
    log_dir: Path,
    db_path: Path | None = None,
    extra: tuple[Path, ...] = (),
) -> None:
    """Core write-fence used by run_digest and every writable service entry point.

    Call before opening databases or creating directories for write.
    """
    validate_mail_root_relationship(newsletter_root, mail_root)
    paths = collect_writable_run_paths(
        output_dir=output_dir,
        state_dir=state_dir,
        log_dir=log_dir,
        db_path=db_path,
        extra=extra,
    )
    assert_safe_write_paths_dual(newsletter_root, mail_root, *paths)


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

    validate_mail_root_relationship(root, mail_root)

    for label, path in [
        ("output_dir", output_dir),
        ("state_dir", state_dir),
        ("log_dir", log_dir),
    ]:
        if root.resolve() == path.resolve():
            raise SafetyError(f"--root must not equal {label}")

    live_newsletters = (mail_root / "Newsletters.sbd").resolve()
    # Case-insensitive match for discovered Thunderbird folder names.
    if not live_newsletters.is_dir():
        try:
            for child in mail_root.iterdir():
                if child.is_dir() and child.name.lower() == "newsletters.sbd":
                    live_newsletters = child.resolve()
                    break
        except OSError:
            pass
    fixture_hint = Path("tests/fixtures/Newsletters.sbd").resolve()
    if root.resolve() == live_newsletters and root.resolve() != fixture_hint:
        warnings.append(
            "WARNING: Reading live Thunderbird data. Recommend testing with:\n"
            "  python -m rollup inventory --root tests/fixtures/Newsletters.sbd\n"
            "Before copying real mail, confirm .gitignore contains fixtures/.\n"
            "Never commit files copied from your live mail root "
            f"({mail_root})."
        )
    return warnings

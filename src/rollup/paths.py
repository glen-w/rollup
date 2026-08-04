"""Resolve Thunderbird mail / newsletter roots with discovery fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rollup.config import DEFAULT_MAIL_ROOT, DEFAULT_NEWSLETTER_ROOT

logger = logging.getLogger(__name__)

NEWSLETTER_DIR_NAMES = frozenset({"newsletters.sbd"})

PathSource = Literal["explicit", "default", "discovered"]


@dataclass(frozen=True)
class ResolvedMailPaths:
    mail_root: Path
    root: Path
    source: PathSource
    candidates: tuple[Path, ...] = ()
    message: str = ""


def _is_newsletters_sbd(path: Path) -> bool:
    return path.is_dir() and path.name.lower() == "newsletters.sbd"


def discover_newsletters_sbd(
    *,
    home: Path | None = None,
) -> list[Path]:
    """Find Thunderbird Newsletters.sbd dirs under common macOS profile paths."""
    home = home if home is not None else Path.home()
    profiles_root = home / "Library" / "Thunderbird" / "Profiles"
    found: list[Path] = []
    if not profiles_root.is_dir():
        return found
    try:
        profile_dirs = sorted(profiles_root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return found
    for profile in profile_dirs:
        if not profile.is_dir() or profile.name.startswith("."):
            continue
        mail_root = profile / "Mail"
        if not mail_root.is_dir():
            continue
        try:
            account_dirs = sorted(mail_root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for account in account_dirs:
            if not account.is_dir() or account.name.startswith("."):
                continue
            try:
                children = list(account.iterdir())
            except OSError:
                continue
            for child in children:
                if _is_newsletters_sbd(child):
                    found.append(child.resolve())
    # Dedupe while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def resolve_mail_paths(
    *,
    root: Path,
    mail_root: Path,
    root_explicit: bool,
    mail_root_explicit: bool,
    home: Path | None = None,
    default_mail_root: Path | None = None,
    default_newsletter_root: Path | None = None,
) -> ResolvedMailPaths:
    """Resolve newsletter root and mail root.

    If both paths were left at defaults and the default newsletter root is
    missing, try Thunderbird profile discovery. Exactly one candidate is used;
    zero or many leave defaults in place with a diagnostic message.

    When only ``root`` is explicit and it looks like a Thunderbird ``*.sbd``
    tree, infer ``mail_root`` as the parent account directory.
    """
    default_mail_root = default_mail_root or DEFAULT_MAIL_ROOT
    default_newsletter_root = default_newsletter_root or DEFAULT_NEWSLETTER_ROOT
    home = home if home is not None else Path.home()

    if root_explicit and not mail_root_explicit:
        expanded_root = root.expanduser()
        if expanded_root.name.lower().endswith(".sbd"):
            inferred_mail = expanded_root.parent
            return ResolvedMailPaths(
                mail_root=inferred_mail,
                root=expanded_root,
                source="explicit",
                message=(
                    f"Inferred mail_root={inferred_mail} from newsletter root parent"
                ),
            )

    if root_explicit or mail_root_explicit:
        return ResolvedMailPaths(
            mail_root=mail_root.expanduser(),
            root=root.expanduser(),
            source="explicit",
        )

    # Back-compat: today's default layout when it exists.
    if default_newsletter_root.expanduser().is_dir():
        return ResolvedMailPaths(
            mail_root=default_mail_root.expanduser(),
            root=default_newsletter_root.expanduser(),
            source="default",
        )

    # Caller already overrode away from package defaults somehow — keep as-is.
    if root.resolve() != default_newsletter_root.expanduser().resolve() or (
        mail_root.resolve() != default_mail_root.expanduser().resolve()
        and mail_root.expanduser().is_dir()
    ):
        if root.expanduser().is_dir():
            return ResolvedMailPaths(
                mail_root=mail_root.expanduser(),
                root=root.expanduser(),
                source="default",
            )

    candidates = discover_newsletters_sbd(home=home)
    if len(candidates) == 1:
        discovered_root = candidates[0]
        discovered_mail = discovered_root.parent
        logger.info(
            "Discovered Thunderbird newsletter root: %s", discovered_root
        )
        return ResolvedMailPaths(
            mail_root=discovered_mail,
            root=discovered_root,
            source="discovered",
            candidates=tuple(candidates),
            message=f"Using discovered newsletter root {discovered_root}",
        )

    if not candidates:
        return ResolvedMailPaths(
            mail_root=mail_root.expanduser(),
            root=root.expanduser(),
            source="default",
            candidates=(),
            message=(
                f"Default newsletter root not found ({default_newsletter_root}); "
                "no Thunderbird Newsletters.sbd discovered. "
                "Set root/mail_root in ~/.config/rollup/config.toml or pass --root."
            ),
        )

    listed = ", ".join(str(p) for p in candidates)
    return ResolvedMailPaths(
        mail_root=mail_root.expanduser(),
        root=root.expanduser(),
        source="default",
        candidates=tuple(candidates),
        message=(
            f"Multiple Thunderbird Newsletters.sbd candidates found: {listed}. "
            "Set root/mail_root in config or pass --root / --mail-root."
        ),
    )

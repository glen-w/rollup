"""Gmail TOML configuration: which labels become digest folders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rollup.source_fetch_cache import MAX_FETCH_TTL_HOURS

GMAIL_TOP_KEYS = frozenset(
    {
        "enabled",
        "labels",
        "label_prefix",
        "strip_prefix",
        "fetch_ttl_hours",
        "max_messages_per_label",
        "extra_query",
    }
)

DEFAULT_GMAIL_FETCH_TTL_HOURS = 6
DEFAULT_MAX_MESSAGES_PER_LABEL = 200
MAX_MESSAGES_PER_LABEL = 1000
MAX_GMAIL_LABELS = 100
# Raw RFC 822 payloads above this are skipped (newsletters with big attachments).
MAX_RAW_MESSAGE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class GmailConfig:
    """``[gmail]`` table.

    ``labels`` lists explicit Gmail label names (``Newsletters/tech``).
    ``label_prefix`` takes every label under a prefix (``Newsletters/``), the
    Gmail equivalent of a ``Newsletters.sbd`` tree. ``strip_prefix`` (defaults
    to ``label_prefix``) is removed from label names to form folder names so
    ``Newsletters/tech`` becomes the folder ``tech``.
    """

    enabled: bool = False
    labels: tuple[str, ...] = ()
    label_prefix: str | None = None
    strip_prefix: str | None = None
    fetch_ttl_hours: int = DEFAULT_GMAIL_FETCH_TTL_HOURS
    max_messages_per_label: int = DEFAULT_MAX_MESSAGES_PER_LABEL
    extra_query: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.labels or self.label_prefix)

    @property
    def effective_strip_prefix(self) -> str | None:
        if self.strip_prefix is not None:
            return self.strip_prefix or None
        return self.label_prefix


@dataclass(frozen=True)
class GmailTarget:
    """One label to ingest. ``label_id`` is resolved lazily against the API."""

    label_name: str
    folder_name: str
    label_id: str | None = None
    is_prefix: bool = False


def normalize_label_name(raw: str) -> str:
    return "/".join(part.strip() for part in raw.strip().strip("/").split("/") if part.strip())


def folder_name_for_label(label_name: str, *, strip_prefix: str | None) -> str:
    """Map a Gmail label name onto a digest folder name."""
    name = normalize_label_name(label_name)
    if strip_prefix:
        prefix = normalize_label_name(strip_prefix)
        if prefix and (name == prefix or name.startswith(prefix + "/")):
            stripped = name[len(prefix) :].strip("/")
            if stripped:
                return stripped
    return name or "(unlabelled)"


def _parse_label_list(raw: object, *, path: Path) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError(f"{path}: [gmail].labels must be a list of strings")
    seen: list[str] = []
    for item in raw:
        name = normalize_label_name(item)
        if not name:
            raise ValueError(f"{path}: [gmail].labels entries must be non-empty")
        if name not in seen:
            seen.append(name)
    if len(seen) > MAX_GMAIL_LABELS:
        raise ValueError(f"{path}: [gmail].labels supports at most {MAX_GMAIL_LABELS} labels")
    return tuple(seen)


def _parse_optional_str(raw: object, *, path: Path, context: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{path}: {context} must be a string")
    return raw.strip() or None


def parse_gmail_config(raw: object | None, *, path: Path) -> GmailConfig:
    if raw is None:
        return GmailConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [gmail] must be a table")
    unknown = set(raw) - GMAIL_TOP_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"{path}: unknown key(s) in [gmail]: {keys}")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"{path}: [gmail].enabled must be a boolean")

    labels = _parse_label_list(raw.get("labels"), path=path)
    label_prefix = _parse_optional_str(
        raw.get("label_prefix"), path=path, context="[gmail].label_prefix"
    )
    if label_prefix is not None:
        label_prefix = normalize_label_name(label_prefix) or None
    strip_prefix = raw.get("strip_prefix")
    if strip_prefix is not None:
        if not isinstance(strip_prefix, str):
            raise ValueError(f"{path}: [gmail].strip_prefix must be a string")
        strip_prefix = normalize_label_name(strip_prefix)

    ttl = raw.get("fetch_ttl_hours", DEFAULT_GMAIL_FETCH_TTL_HOURS)
    if not isinstance(ttl, int) or isinstance(ttl, bool):
        raise ValueError(f"{path}: [gmail].fetch_ttl_hours must be an integer")
    if ttl < 0 or ttl > MAX_FETCH_TTL_HOURS:
        raise ValueError(
            f"{path}: [gmail].fetch_ttl_hours must be between 0 and {MAX_FETCH_TTL_HOURS}"
        )

    max_msgs = raw.get("max_messages_per_label", DEFAULT_MAX_MESSAGES_PER_LABEL)
    if not isinstance(max_msgs, int) or isinstance(max_msgs, bool):
        raise ValueError(f"{path}: [gmail].max_messages_per_label must be an integer")
    if max_msgs < 1 or max_msgs > MAX_MESSAGES_PER_LABEL:
        raise ValueError(
            f"{path}: [gmail].max_messages_per_label must be between 1 and "
            f"{MAX_MESSAGES_PER_LABEL}"
        )

    extra_query = _parse_optional_str(
        raw.get("extra_query"), path=path, context="[gmail].extra_query"
    )

    return GmailConfig(
        enabled=enabled,
        labels=labels,
        label_prefix=label_prefix,
        strip_prefix=strip_prefix,
        fetch_ttl_hours=ttl,
        max_messages_per_label=max_msgs,
        extra_query=extra_query,
    )


def _folder_allowed(
    folder: str,
    *,
    folders_include: tuple[str, ...],
    folders_exclude: tuple[str, ...],
) -> bool:
    if folders_include and folder not in set(folders_include):
        return False
    if folder in set(folders_exclude):
        return False
    return True


def targets_from_config(
    config: GmailConfig,
    *,
    folders_include: tuple[str, ...],
    folders_exclude: tuple[str, ...],
    known_labels: dict[str, str] | None = None,
) -> tuple[GmailTarget, ...]:
    """Build ingest targets from TOML; ``known_labels`` maps name → id from cache.

    Explicit ``labels`` become concrete targets. ``label_prefix`` expands to
    cached labels under that prefix when known, otherwise to one prefix
    target that the fetch stage expands against the live label list.
    """
    known = known_labels or {}
    strip = config.effective_strip_prefix
    out: list[GmailTarget] = []
    seen: set[str] = set()

    def _add(name: str, *, is_prefix: bool = False) -> None:
        if name in seen:
            return
        folder = folder_name_for_label(name, strip_prefix=strip)
        if not is_prefix and not _folder_allowed(
            folder, folders_include=folders_include, folders_exclude=folders_exclude
        ):
            return
        seen.add(name)
        out.append(
            GmailTarget(
                label_name=name,
                folder_name=folder,
                label_id=known.get(name),
                is_prefix=is_prefix,
            )
        )

    for name in config.labels:
        _add(name)
    if config.label_prefix:
        prefix = config.label_prefix
        under = sorted(n for n in known if n == prefix or n.startswith(prefix + "/"))
        if under:
            for name in under:
                _add(name)
        else:
            _add(prefix, is_prefix=True)
    return tuple(out[:MAX_GMAIL_LABELS])


def expand_prefix_targets(
    targets: tuple[GmailTarget, ...],
    *,
    config: GmailConfig,
    live_labels: dict[str, str],
    folders_include: tuple[str, ...],
    folders_exclude: tuple[str, ...],
) -> tuple[GmailTarget, ...]:
    """Resolve label ids and expand prefix targets using the live label list."""
    strip = config.effective_strip_prefix
    out: list[GmailTarget] = []
    seen: set[str] = set()
    for target in targets:
        names: list[str]
        if target.is_prefix:
            prefix = target.label_name
            names = sorted(
                n for n in live_labels if n == prefix or n.startswith(prefix + "/")
            )
        else:
            names = [target.label_name]
        for name in names:
            if name in seen:
                continue
            folder = folder_name_for_label(name, strip_prefix=strip)
            if not _folder_allowed(
                folder, folders_include=folders_include, folders_exclude=folders_exclude
            ):
                continue
            seen.add(name)
            out.append(
                GmailTarget(
                    label_name=name,
                    folder_name=folder,
                    label_id=live_labels.get(name),
                )
            )
    return tuple(out[:MAX_GMAIL_LABELS])


def list_gmail_folder_names(
    config: GmailConfig | None,
    *,
    include: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Folder names for explicit labels (prefix labels need the API or cache)."""
    if config is None or not config.enabled:
        return []
    targets = targets_from_config(
        config, folders_include=tuple(include), folders_exclude=tuple(exclude)
    )
    return [t.folder_name for t in targets if not t.is_prefix]

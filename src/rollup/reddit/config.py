"""Reddit configuration models and TOML parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

REDDIT_FOLDER_PREFIX = "reddit:"
REDDIT_FEED_FOLDER = "reddit:feed"

RedditLayout = Literal["feed", "per_source"]
RedditSort = Literal["hot", "new", "top", "rising", "controversial"]
RedditMode = Literal["summary", "posts"]
LinkedInLayout = Literal["feed", "per_source", "per_search"]

REDDIT_SORTS = frozenset({"hot", "new", "top", "rising", "controversial"})
REDDIT_MODES = frozenset({"summary", "posts"})
REDDIT_LAYOUTS = frozenset({"feed", "per_source"})
LINKEDIN_LAYOUTS = frozenset({"feed", "per_source", "per_search"})

DEFAULT_REDDIT_SORT: RedditSort = "hot"
DEFAULT_REDDIT_LIMIT = 10
MAX_REDDIT_LIMIT = 50
MAX_REDDIT_SUBS = 100

SUB_KEYS = frozenset({"enabled", "mode", "sort", "limit", "display_name", "emoji", "accent", "order"})
REDDIT_TOP_KEYS = frozenset(
    {"enabled", "layout", "sort", "limit", "mode", "time_filter", "subs"}
)


@dataclass(frozen=True)
class RedditSub:
    name: str
    enabled: bool = False
    mode: RedditMode | None = None
    sort: RedditSort | None = None
    limit: int | None = None
    display_name: str | None = None
    emoji: str | None = None
    accent: str | None = None
    order: int | None = None

    def resolved_mode(self, global_mode: RedditMode) -> RedditMode:
        return self.mode if self.mode is not None else global_mode

    def resolved_sort(self, global_sort: RedditSort) -> RedditSort:
        return self.sort if self.sort is not None else global_sort

    def resolved_limit(self, global_limit: int) -> int:
        if self.limit is not None:
            return min(max(1, self.limit), MAX_REDDIT_LIMIT)
        return min(max(1, global_limit), MAX_REDDIT_LIMIT)


@dataclass(frozen=True)
class RedditConfig:
    enabled: bool = False
    layout: RedditLayout = "feed"
    sort: RedditSort = DEFAULT_REDDIT_SORT
    limit: int = DEFAULT_REDDIT_LIMIT
    mode: RedditMode = "summary"
    time_filter: str | None = None
    subs: dict[str, RedditSub] = field(default_factory=dict)


def folder_name_for_sub(sub_name: str, *, layout: RedditLayout) -> str:
    if layout == "feed":
        return REDDIT_FEED_FOLDER
    return f"{REDDIT_FOLDER_PREFIX}{sub_name.strip().lower()}"


def sub_from_folder_name(folder_name: str) -> str | None:
    if folder_name == REDDIT_FEED_FOLDER:
        return None
    if not folder_name.startswith(REDDIT_FOLDER_PREFIX):
        return None
    sub = folder_name[len(REDDIT_FOLDER_PREFIX) :].strip()
    return sub or None


def lookback_to_time_filter(lookback_days: int) -> str:
    if lookback_days <= 1:
        return "day"
    if lookback_days <= 7:
        return "week"
    if lookback_days <= 31:
        return "month"
    return "year"


def _strip_subreddit_prefix(name: str) -> str:
    """Strip a leading ``r/`` prefix only (not arbitrary leading ``r`` characters)."""
    if name.startswith("r/"):
        return name[2:]
    return name


def _parse_sub_name(raw: str, *, path: Path, context: str) -> str:
    name = _strip_subreddit_prefix(raw.strip().lower())
    if not name or "/" in name:
        raise ValueError(f"{path}: {context} must be a non-empty subreddit name")
    return name


def normalize_sub_name(raw: str) -> str | None:
    """Return a normalized subreddit slug or None when invalid."""
    name = _strip_subreddit_prefix(raw.strip().lower())
    if not name or "/" in name:
        return None
    return name


def _parse_mode(value: object, *, path: Path, context: str) -> RedditMode:
    if value not in REDDIT_MODES:
        raise ValueError(f"{path}: {context} must be summary or posts")
    return value  # type: ignore[return-value]


def _parse_sort(value: object, *, path: Path, context: str) -> RedditSort:
    if value not in REDDIT_SORTS:
        raise ValueError(f"{path}: {context} must be one of {sorted(REDDIT_SORTS)}")
    return value  # type: ignore[return-value]


def _parse_limit(value: object, *, path: Path, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{path}: {context} must be an integer")
    if value < 1 or value > MAX_REDDIT_LIMIT:
        raise ValueError(f"{path}: {context} must be between 1 and {MAX_REDDIT_LIMIT}")
    return value


def parse_reddit_config(raw: object | None, *, path: Path) -> RedditConfig:
    if raw is None:
        return RedditConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [reddit] must be a table")

    unknown = set(raw) - REDDIT_TOP_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"{path}: unknown key(s) in [reddit]: {keys}")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"{path}: [reddit].enabled must be a boolean")

    layout = raw.get("layout", "feed")
    if layout not in REDDIT_LAYOUTS:
        raise ValueError(f"{path}: [reddit].layout must be feed or per_source")

    sort = _parse_sort(raw.get("sort", DEFAULT_REDDIT_SORT), path=path, context="[reddit].sort")
    limit = _parse_limit(raw.get("limit", DEFAULT_REDDIT_LIMIT), path=path, context="[reddit].limit")
    mode = _parse_mode(raw.get("mode", "summary"), path=path, context="[reddit].mode")

    time_filter = raw.get("time_filter")
    if time_filter is not None and not isinstance(time_filter, str):
        raise ValueError(f"{path}: [reddit].time_filter must be a string")

    subs_raw = raw.get("subs")
    subs: dict[str, RedditSub] = {}
    if subs_raw is not None:
        if not isinstance(subs_raw, dict):
            raise ValueError(f"{path}: [reddit.subs] must be a table")
        for name_raw, body in subs_raw.items():
            slug = _parse_sub_name(str(name_raw), path=path, context=f"[reddit.subs.{name_raw}]")
            if not isinstance(body, dict):
                raise ValueError(f"{path}: [reddit.subs.{slug}] must be a table")
            sub_unknown = set(body) - SUB_KEYS
            if sub_unknown:
                keys = ", ".join(sorted(sub_unknown))
                raise ValueError(f"{path}: unknown key(s) in [reddit.subs.{slug}]: {keys}")
            sub_enabled = body.get("enabled", False)
            if not isinstance(sub_enabled, bool):
                raise ValueError(f"{path}: [reddit.subs.{slug}].enabled must be a boolean")
            sub_mode = body.get("mode")
            if sub_mode is not None:
                sub_mode = _parse_mode(sub_mode, path=path, context=f"[reddit.subs.{slug}].mode")
            sub_sort = body.get("sort")
            if sub_sort is not None:
                sub_sort = _parse_sort(sub_sort, path=path, context=f"[reddit.subs.{slug}].sort")
            sub_limit = body.get("limit")
            if sub_limit is not None:
                sub_limit = _parse_limit(sub_limit, path=path, context=f"[reddit.subs.{slug}].limit")
            display_name = body.get("display_name")
            if display_name is not None and not isinstance(display_name, str):
                raise ValueError(f"{path}: [reddit.subs.{slug}].display_name must be a string")
            emoji = body.get("emoji")
            accent = body.get("accent")
            order = body.get("order")
            if emoji is not None and not isinstance(emoji, str):
                raise ValueError(f"{path}: [reddit.subs.{slug}].emoji must be a string")
            if accent is not None and not isinstance(accent, str):
                raise ValueError(f"{path}: [reddit.subs.{slug}].accent must be a string")
            if order is not None and (not isinstance(order, int) or isinstance(order, bool)):
                raise ValueError(f"{path}: [reddit.subs.{slug}].order must be an integer")
            subs[slug] = RedditSub(
                name=slug,
                enabled=sub_enabled,
                mode=sub_mode,
                sort=sub_sort,
                limit=sub_limit,
                display_name=display_name.strip() if display_name else None,
                emoji=emoji,
                accent=accent,
                order=order,
            )

    return RedditConfig(
        enabled=enabled,
        layout=layout,  # type: ignore[arg-type]
        sort=sort,
        limit=limit,
        mode=mode,
        time_filter=time_filter.strip() if time_filter else None,
        subs=subs,
    )


def filter_reddit_subs(
    config: RedditConfig,
    *,
    folders_include: tuple[str, ...],
    folders_exclude: tuple[str, ...],
) -> tuple[RedditSub, ...]:
    """Return enabled subs after folder include/exclude filters."""
    result = [s for s in config.subs.values() if s.enabled]
    if folders_include:
        include_set = set(folders_include)
        filtered: list[RedditSub] = []
        for sub in result:
            folder = folder_name_for_sub(sub.name, layout=config.layout)
            if folder in include_set:
                filtered.append(sub)
            elif config.layout == "feed" and REDDIT_FEED_FOLDER in include_set:
                filtered.append(sub)
        result = filtered
    if folders_exclude:
        exclude_set = set(folders_exclude)
        result = [
            s
            for s in result
            if folder_name_for_sub(s.name, layout=config.layout) not in exclude_set
            and not (config.layout == "feed" and REDDIT_FEED_FOLDER in exclude_set)
        ]
    return tuple(sorted(result[:MAX_REDDIT_SUBS], key=lambda s: s.name))


def list_reddit_folder_names(
    reddit_config: RedditConfig | None,
    *,
    include: tuple[str, ...] | list[str] = (),
    exclude: tuple[str, ...] | list[str] = (),
) -> list[str]:
    if reddit_config is None or not reddit_config.enabled:
        return []
    subs = filter_reddit_subs(
        reddit_config,
        folders_include=tuple(include),
        folders_exclude=tuple(exclude),
    )
    if not subs:
        return []
    if reddit_config.layout == "feed":
        return [REDDIT_FEED_FOLDER]
    return [folder_name_for_sub(s.name, layout="per_source") for s in subs]

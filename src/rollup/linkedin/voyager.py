"""Voyager profileUpdatesV2 client for fromMember content searches.

Keyword CONTENT search via voyagerSearchDashClusters currently returns an empty
FeedbackCard. Author-scoped feeds still work. Isolated so LinkedIn rotations
stay in this module.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from rollup.error_sanitize import sanitize_provider_message
from rollup.linkedin.models import LinkedInPost
from rollup.linkedin.session import voyager_headers

logger = logging.getLogger(__name__)

PROFILE_UPDATES_URL = "https://www.linkedin.com/voyager/api/identity/profileUpdatesV2"
MAX_PAGES_PER_MEMBER = 2
POSTS_PER_PAGE = 10
MAX_MEMBERS = 20
MAX_POSTS = 100
REQUEST_TIMEOUT = 30
BACKOFF_SECONDS = 2.0
UPDATE_V2_KEY = "com.linkedin.voyager.feed.render.UpdateV2"

_ACTIVITY_URN_RE = re.compile(r"urn:li:activity:(\d+)")
_MEMBER_URN_RE = re.compile(r"urn:li:(?:member|fsd_profile):([A-Za-z0-9_-]+)")


class VoyagerFetchError(RuntimeError):
    """Voyager HTTP or parse failure."""


def _activity_id_from_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    m = _ACTIVITY_URN_RE.search(text)
    if m:
        return m.group(1)
    if text.isdigit():
        return text
    return None


def _member_id_from_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    m = _MEMBER_URN_RE.search(text)
    if m:
        return m.group(1)
    if text.startswith("ACo") and len(text) > 10:
        return text
    return None


def _parse_iso_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _nested_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        inner = value.get("text")
        if isinstance(inner, dict):
            return str(inner.get("text") or inner.get("accessibilityText") or "").strip()
        if isinstance(inner, str):
            return inner.strip()
        return str(value.get("accessibilityText") or "").strip()
    return ""


def _created_at(raw: Any) -> datetime | None:
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
    if raw is None:
        return None
    return _parse_iso_datetime(str(raw))


def created_at_from_activity_id(activity_id: str | None) -> datetime | None:
    """Decode LinkedIn snowflake activity ids (timestamp ms in the high bits)."""
    if not activity_id or not str(activity_id).isdigit():
        return None
    ts_ms = int(activity_id) >> 22
    # Milliseconds between ~2001 and ~2033.
    if ts_ms < 1_000_000_000_000 or ts_ms > 2_000_000_000_000:
        return None
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def post_from_update_v2(
    update: dict[str, Any],
    *,
    fallback_member_id: str | None = None,
) -> LinkedInPost | None:
    """Map a Voyager UpdateV2 (or wrapper) to LinkedInPost."""
    if not isinstance(update, dict):
        return None
    if UPDATE_V2_KEY in update and isinstance(update[UPDATE_V2_KEY], dict):
        update = update[UPDATE_V2_KEY]
    value = update.get("value")
    if isinstance(value, dict) and UPDATE_V2_KEY in value:
        nested = value[UPDATE_V2_KEY]
        if isinstance(nested, dict):
            update = nested

    commentary = update.get("commentary") or {}
    text = _nested_text(commentary)
    if not text:
        text = _nested_text(update.get("commentaryText") or {})
    if not text:
        return None

    meta = update.get("updateMetadata") if isinstance(update.get("updateMetadata"), dict) else {}
    activity_id = _activity_id_from_value(
        meta.get("urn")
        or meta.get("id")
        or update.get("entityUrn")
        or update.get("urn")
    )
    actor = update.get("actor") if isinstance(update.get("actor"), dict) else {}
    name_obj = actor.get("name")
    if isinstance(name_obj, dict):
        author_name = str(name_obj.get("text") or "(unknown)")
    elif isinstance(name_obj, str) and name_obj.strip():
        author_name = name_obj.strip()
    else:
        author_name = "(unknown)"
    member_id = _member_id_from_value(
        actor.get("urn") or actor.get("entityUrn") or actor.get("backendUrn")
    ) or fallback_member_id
    created_at = _created_at(update.get("createdAt") or meta.get("createdAt"))
    if created_at is None:
        created_at = created_at_from_activity_id(activity_id)
    permalink = ""
    social = update.get("socialContent") if isinstance(update.get("socialContent"), dict) else {}
    share_url = social.get("shareUrl")
    if isinstance(share_url, str) and share_url.startswith("https://"):
        permalink = share_url.split("?", 1)[0]
    elif activity_id:
        permalink = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}"
    return LinkedInPost(
        activity_id=activity_id,
        author_name=author_name,
        author_member_id=member_id,
        text=text,
        permalink=permalink,
        created_at=created_at,
    )


def _included_by_urn(included: object) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not isinstance(included, list):
        return index
    for raw in included:
        if not isinstance(raw, dict):
            continue
        urn = raw.get("entityUrn") or raw.get("urn")
        if isinstance(urn, str) and urn:
            index[urn] = raw
    return index


def posts_from_profile_updates_payload(
    payload: object,
    *,
    fallback_member_id: str | None = None,
) -> list[LinkedInPost]:
    """Parse profileUpdatesV2 JSON (elements and/or included).

    Normalized Rest.li payloads put feed item URNs in ``data['*elements']``
    and the UpdateV2 objects in ``included``. Nested reshares also appear in
    ``included``; only the starred element URNs are primary feed items.
    """
    if not isinstance(payload, dict):
        return []
    posts: list[LinkedInPost] = []
    seen: set[str] = set()

    def _add(raw: object) -> None:
        if not isinstance(raw, dict):
            return
        post = post_from_update_v2(raw, fallback_member_id=fallback_member_id)
        if post is None or not post.text:
            return
        key = post.activity_id or post.text
        if key in seen:
            return
        seen.add(key)
        posts.append(post)

    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    star = (data or {}).get("*elements") if data is not None else None
    included = payload.get("included") or []
    if isinstance(star, list) and star and all(isinstance(item, str) for item in star):
        by_urn = _included_by_urn(included)
        for urn in star:
            _add(by_urn.get(urn))
        return posts

    for raw in payload.get("elements") or []:
        _add(raw)
    for raw in included:
        _add(raw)
    return posts


def fetch_member_updates(
    session: requests.Session,
    member_id: str,
    *,
    max_posts: int = POSTS_PER_PAGE * MAX_PAGES_PER_MEMBER,
) -> list[LinkedInPost]:
    """Fetch recent posts for one fsd_profile / ACo… member id."""
    posts: list[LinkedInPost] = []
    start = 0
    profile_urn = f"urn:li:fsd_profile:{member_id}"
    for page in range(MAX_PAGES_PER_MEMBER):
        if len(posts) >= max_posts:
            break
        params = {
            "q": "memberShareFeed",
            "moduleKey": "member-shares:phone",
            "includeLongTermHistory": "true",
            "profileUrn": profile_urn,
            "count": str(POSTS_PER_PAGE),
            "start": str(start),
        }
        try:
            response = session.get(
                PROFILE_UPDATES_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers=voyager_headers(session),
            )
        except requests.RequestException as exc:
            raise VoyagerFetchError(sanitize_provider_message(str(exc))) from exc
        if response.status_code == 401:
            raise VoyagerFetchError("LinkedIn Voyager 401 (session expired or invalid)")
        if response.status_code == 429:
            raise VoyagerFetchError("LinkedIn returned 429 (rate limited)")
        if response.status_code >= 400:
            raise VoyagerFetchError(
                f"LinkedIn Voyager HTTP {response.status_code}: "
                f"{sanitize_provider_message(response.text[:200])}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise VoyagerFetchError("LinkedIn Voyager returned non-JSON") from exc
        batch = posts_from_profile_updates_payload(
            payload, fallback_member_id=member_id
        )
        if not batch:
            break
        posts.extend(batch)
        start += POSTS_PER_PAGE
        if page + 1 < MAX_PAGES_PER_MEMBER:
            time.sleep(BACKOFF_SECONDS)
    return posts[:max_posts]


def fetch_from_member_posts(
    session: requests.Session,
    member_ids: tuple[str, ...],
) -> list[LinkedInPost]:
    """Fetch posts for each fromMember id; skip individual member failures."""
    seen: set[str] = set()
    posts: list[LinkedInPost] = []
    failures = 0
    ids = member_ids[:MAX_MEMBERS]
    remaining = MAX_POSTS
    for i, member_id in enumerate(ids):
        if remaining <= 0:
            break
        try:
            batch = fetch_member_updates(session, member_id, max_posts=min(20, remaining))
        except VoyagerFetchError as exc:
            failures += 1
            logger.warning(
                "LinkedIn Voyager failed for member %s: %s",
                member_id[:12],
                sanitize_provider_message(str(exc)),
            )
            continue
        for post in batch:
            key = post.activity_id or post.text
            if key in seen:
                continue
            seen.add(key)
            posts.append(post)
            remaining -= 1
            if remaining <= 0:
                break
        if i + 1 < len(ids) and remaining > 0:
            time.sleep(BACKOFF_SECONDS)
    if not posts and failures:
        raise VoyagerFetchError(
            "LinkedIn Voyager failed for every fromMember in this search"
        )
    if not posts and ids:
        logger.warning("LinkedIn Voyager returned no posts for %d member(s)", len(ids))
    return posts[:MAX_POSTS]

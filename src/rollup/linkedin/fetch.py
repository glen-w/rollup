"""Isolated LinkedIn HTTP client — replace when LinkedIn changes."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import requests

from rollup.error_sanitize import sanitize_provider_message
from rollup.linkedin.config import LinkedInSearch
from rollup.linkedin.models import LinkedInPost
from rollup.linkedin.session import (
    LinkedInSessionError,
    build_linkedin_session,
    jsession_id_configured,
)
from rollup.linkedin.url import apply_lookback_to_url, from_member_ids

logger = logging.getLogger(__name__)

MAX_PAGES = 5
MAX_POSTS_PER_SEARCH = 100
REQUEST_TIMEOUT = 30
BACKOFF_SECONDS = 2.0

_ACTIVITY_URN_RE = re.compile(r"urn:li:activity:(\d+)")
_ACTIVITY_URL_RE = re.compile(r"activity[:-](\d+)", re.IGNORECASE)
_MEMBER_URN_RE = re.compile(r"urn:li:(?:member|fsd_profile):([A-Za-z0-9_-]+)")
# LinkedIn nests post text: commentary.text.text (not commentary.text string).
_COMMENTARY_NESTED_TEXT_RE = re.compile(
    r'"commentary"\s*:\s*\{\s*"text"\s*:\s*\{\s*"text"\s*:\s*"((?:\\.|[^"\\])*)"',
    re.DOTALL,
)
_COMMENTARY_ACCESSIBILITY_RE = re.compile(
    r'"accessibilityText"\s*:\s*"((?:\\.|[^"\\])*)"',
    re.DOTALL,
)
_CREATED_AT_RE = re.compile(r'"createdAt"\s*:\s*(\d+)')
_NAME_TEXT_RE = re.compile(
    r'"name"\s*:\s*\{\s*"text"\s*:\s*"((?:\\.|[^"\\])*)"',
    re.DOTALL,
)


class LinkedInFetchError(RuntimeError):
    """LinkedIn fetch failed (auth, rate limit, checkpoint, parse)."""


class LinkedInClient(Protocol):
    def fetch_search(
        self, search: LinkedInSearch, *, lookback_days: int
    ) -> list[LinkedInPost]:
        ...


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


def _unescape_json_string(text: str) -> str:
    try:
        return json.loads(f'"{text}"')
    except json.JSONDecodeError:
        return text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


def _extract_json_array_after(html: str, marker: str) -> list | None:
    """Return parsed JSON array after ``marker`` (e.g. ``"included":``), or None."""
    idx = html.find(marker)
    if idx < 0:
        return None
    start = html.find("[", idx + len(marker))
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for pos in range(start, len(html)):
        ch = html[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(html[start : pos + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, list) else None
    return None


def _epoch_ms_to_datetime(raw: str) -> datetime | None:
    try:
        ms = int(raw)
    except ValueError:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _extract_text_from_commentary_obj(entity: dict) -> str:
    text_obj = entity.get("text")
    if isinstance(text_obj, dict):
        return str(text_obj.get("text") or text_obj.get("accessibilityText") or "").strip()
    if isinstance(text_obj, str):
        return text_obj.strip()
    return str(entity.get("accessibilityText") or "").strip()


def _post_from_voyager_item(item: dict) -> LinkedInPost | None:
    if not isinstance(item, dict):
        return None
    entity = item.get("commentary") or item.get("message") or item
    if not isinstance(entity, dict):
        return None
    text = _extract_text_from_commentary_obj(entity)
    if not text:
        return None
    activity_id = _activity_id_from_value(
        entity.get("entityUrn")
        or entity.get("urn")
        or item.get("entityUrn")
        or item.get("urn")
    )
    author = item.get("actor") or item.get("author") or item.get("socialDetail", {})
    if isinstance(author, dict) and "actor" in author:
        author = author.get("actor") or author
    author_name = "(unknown)"
    member_id = None
    if isinstance(author, dict):
        name_obj = author.get("name")
        if isinstance(name_obj, dict):
            author_name = str(name_obj.get("text") or "(unknown)")
        elif isinstance(name_obj, str):
            author_name = name_obj
        member_id = _member_id_from_value(
            author.get("urn") or author.get("entityUrn") or author.get("backendUrn")
        )
    created_raw = entity.get("createdAt") or item.get("createdAt")
    created_at = None
    if isinstance(created_raw, (int, float)):
        created_at = datetime.fromtimestamp(float(created_raw) / 1000.0, tz=timezone.utc)
    elif created_raw is not None:
        created_at = _parse_iso_datetime(str(created_raw))
    permalink = ""
    if activity_id:
        permalink = (
            f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}"
        )
    return LinkedInPost(
        activity_id=activity_id,
        author_name=author_name,
        author_member_id=member_id,
        text=text,
        permalink=permalink,
        created_at=created_at,
    )


def _posts_from_included_list(included: list) -> list[LinkedInPost]:
    posts: list[LinkedInPost] = []
    seen: set[str] = set()
    for item in included:
        post = _post_from_voyager_item(item)
        if post is None or not post.text:
            continue
        key = post.activity_id or post.text
        if key in seen:
            continue
        seen.add(key)
        posts.append(post)
        if len(posts) >= MAX_POSTS_PER_SEARCH:
            break
    return posts


def _posts_from_commentary_regexes(html: str) -> list[LinkedInPost]:
    posts: list[LinkedInPost] = []
    seen: set[str] = set()
    for pattern in (_COMMENTARY_NESTED_TEXT_RE, _COMMENTARY_ACCESSIBILITY_RE):
        for match in pattern.finditer(html):
            text = _unescape_json_string(match.group(1)).strip()
            if not text or len(text) < 2 or text in seen:
                continue
            seen.add(text)
            window_start = max(0, match.start() - 4000)
            window_end = min(len(html), match.end() + 4000)
            window = html[window_start:window_end]
            activity_id = None
            urns = _ACTIVITY_URN_RE.findall(window)
            if urns:
                activity_id = urns[0]
            author_name = "(unknown)"
            name_match = _NAME_TEXT_RE.search(window)
            if name_match:
                author_name = _unescape_json_string(name_match.group(1)).strip()
            member_id = None
            members = _MEMBER_URN_RE.findall(window)
            if members:
                member_id = members[0]
            created_at = None
            created_match = _CREATED_AT_RE.search(window)
            if created_match:
                created_at = _epoch_ms_to_datetime(created_match.group(1))
            permalink = ""
            if activity_id:
                permalink = (
                    f"https://www.linkedin.com/feed/update/"
                    f"urn:li:activity:{activity_id}"
                )
            posts.append(
                LinkedInPost(
                    activity_id=activity_id,
                    author_name=author_name,
                    author_member_id=member_id,
                    text=text,
                    permalink=permalink,
                    created_at=created_at,
                )
            )
            if len(posts) >= MAX_POSTS_PER_SEARCH:
                return posts
    return posts


def _activity_id_from_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    m = _ACTIVITY_URN_RE.search(text)
    if m:
        return m.group(1)
    m = _ACTIVITY_URL_RE.search(text)
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


def _post_from_dict(raw: dict) -> LinkedInPost | None:
    activity_id = _activity_id_from_value(
        raw.get("activity_id") or raw.get("activityId") or raw.get("urn")
    )
    text = (
        raw.get("text")
        or raw.get("commentary")
        or raw.get("body")
        or ""
    )
    if isinstance(text, dict):
        text = text.get("text") or text.get("accessibilityText") or ""
    text = str(text).strip()
    author_name = (
        raw.get("author_name")
        or raw.get("authorName")
        or raw.get("author")
        or "(unknown)"
    )
    if isinstance(author_name, dict):
        author_name = (
            author_name.get("name")
            or author_name.get("title")
            or "(unknown)"
        )
    author_member_id = _member_id_from_value(
        raw.get("author_member_id")
        or raw.get("authorMemberId")
        or raw.get("member_id")
    )
    permalink = str(
        raw.get("permalink")
        or raw.get("url")
        or raw.get("link")
        or ""
    ).strip()
    if activity_id and not permalink:
        permalink = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}"
    created_at = _parse_iso_datetime(
        raw.get("created_at") or raw.get("createdAt") or raw.get("posted_at")
    )
    if not text and not activity_id:
        return None
    return LinkedInPost(
        activity_id=activity_id,
        author_name=str(author_name),
        author_member_id=author_member_id,
        text=text,
        permalink=permalink,
        created_at=created_at,
    )


def posts_from_fixture_payload(payload: object) -> list[LinkedInPost]:
    """Parse normalized fixture JSON (list or {elements: [...]})."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("elements") or payload.get("posts") or []
    else:
        return []
    posts: list[LinkedInPost] = []
    for raw in items:
        if isinstance(raw, dict):
            post = _post_from_dict(raw)
            if post is not None:
                posts.append(post)
    return posts


def _posts_from_embedded_json(html: str) -> list[LinkedInPost]:
    """Best-effort extraction from LinkedIn search HTML embedded payloads."""
    for marker in ('"included":', '"included" :'):
        included = _extract_json_array_after(html, marker)
        if included:
            posts = _posts_from_included_list(included)
            if posts:
                return posts[:MAX_POSTS_PER_SEARCH]

    posts = _posts_from_commentary_regexes(html)
    if posts:
        return posts[:MAX_POSTS_PER_SEARCH]

    return []


def _check_response_status(response: requests.Response) -> None:
    if response.status_code == 401:
        raise LinkedInFetchError("LinkedIn returned 401 (session expired or invalid)")
    if response.status_code == 429:
        raise LinkedInFetchError("LinkedIn returned 429 (rate limited)")
    if response.status_code >= 400:
        raise LinkedInFetchError(
            f"LinkedIn HTTP {response.status_code}: "
            f"{sanitize_provider_message(response.text[:200])}"
        )
    lowered = response.text[:8000].lower()
    if "authwall" in lowered or "checkpoint" in lowered or "security verification" in lowered:
        raise LinkedInFetchError(
            "LinkedIn checkpoint or login wall detected — refresh session cookie"
        )


class HttpLinkedInClient:
    """Live LinkedIn fetcher (opt-in network)."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], requests.Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or build_linkedin_session

    def fetch_search(
        self, search: LinkedInSearch, *, lookback_days: int
    ) -> list[LinkedInPost]:
        from rollup.linkedin.voyager import VoyagerFetchError, fetch_from_member_posts

        try:
            session = self._session_factory()
        except LinkedInSessionError as exc:
            raise LinkedInFetchError(str(exc)) from exc

        member_ids = from_member_ids(search.url)
        if member_ids:
            if not jsession_id_configured():
                raise LinkedInFetchError(
                    "LinkedIn fromMember search requires ROLLUP_LINKEDIN_JSESSIONID "
                    "(Voyager CSRF; copy JSESSIONID from the same cookie pane as li_at)"
                )
            logger.info(
                "Fetching LinkedIn fromMember feed (%d authors) via Voyager",
                len(member_ids),
            )
            try:
                return fetch_from_member_posts(session, member_ids)
            except VoyagerFetchError as exc:
                raise LinkedInFetchError(str(exc)) from exc

        url = apply_lookback_to_url(search.url, lookback_days)
        posts: list[LinkedInPost] = []
        start = 0
        for page in range(MAX_PAGES):
            page_url = url
            if start > 0:
                sep = "&" if "?" in url else "?"
                page_url = f"{url}{sep}start={start}"
            try:
                response = session.get(page_url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                raise LinkedInFetchError(
                    sanitize_provider_message(str(exc))
                ) from exc
            _check_response_status(response)
            batch = _posts_from_embedded_json(response.text)
            if not batch:
                break
            posts.extend(batch)
            if len(posts) >= MAX_POSTS_PER_SEARCH:
                break
            start += len(batch)
            if page + 1 < MAX_PAGES:
                time.sleep(BACKOFF_SECONDS)
        return posts[:MAX_POSTS_PER_SEARCH]


class FixtureLinkedInClient:
    """Test/dry-run client reading fixture JSON files."""

    def __init__(self, fixture_path: Path) -> None:
        self._fixture_path = fixture_path

    def fetch_search(
        self, search: LinkedInSearch, *, lookback_days: int
    ) -> list[LinkedInPost]:
        del lookback_days
        if not self._fixture_path.is_file():
            return []
        payload = json.loads(self._fixture_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and search.slug in payload:
            return posts_from_fixture_payload(payload[search.slug])
        return posts_from_fixture_payload(payload)


_default_client: LinkedInClient | None = None


def set_default_client(client: LinkedInClient | None) -> None:
    global _default_client
    _default_client = client


def get_default_client() -> LinkedInClient:
    if _default_client is not None:
        return _default_client
    return HttpLinkedInClient()


def fetch_search_posts(
    search: LinkedInSearch,
    *,
    lookback_days: int,
    client: LinkedInClient | None = None,
) -> list[LinkedInPost]:
    fetcher = client or get_default_client()
    logger.info("Fetching LinkedIn search %s", search.slug)
    return fetcher.fetch_search(search, lookback_days=lookback_days)

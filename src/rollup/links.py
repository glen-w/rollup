"""Deterministic offline link classification and rendering helpers."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rollup.models import ClassifiedLink, LinkCategory, LinkItem, LinkRenderBundle

TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "token",
    "r",
    "ref",
    "source",
}

MEANINGFUL_LABEL_MAX_LEN = 80
PRIMARY_CATEGORIES: tuple[LinkCategory, ...] = (
    "primary_content",
    "registration",
    "event",
    "document_pdf",
    "video_audio",
    "content",
    "calendar",
    "author_profile",
    "unknown",
)
HIDDEN_CATEGORIES: tuple[LinkCategory, ...] = (
    "tracking_pixel",
    "junk",
    "unsubscribe_preferences",
    "share_comment_like",
)
PRIORITY_BY_CATEGORY: dict[LinkCategory, int] = {
    "primary_content": 0,
    "registration": 1,
    "event": 2,
    "document_pdf": 3,
    "video_audio": 4,
    "content": 5,
    "calendar": 6,
    "author_profile": 7,
    "unknown": 8,
    "share_comment_like": 9,
    "unsubscribe_preferences": 10,
    "tracking_pixel": 11,
    "junk": 12,
}
URLISH_TEXT_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
PUNCT_ONLY_RE = re.compile(r"^[^\w]+$", re.UNICODE)
BARE_NUMBER_RE = re.compile(r"^\d+$")
GENERIC_ANCHOR_TEXT = {
    "here",
    "click",
}


def clean_anchor_text(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = WHITESPACE_RE.sub(" ", text).strip()
    if not cleaned:
        return None
    if len(cleaned) > MEANINGFUL_LABEL_MAX_LEN:
        return None
    if is_raw_url_text(cleaned):
        return None
    if PUNCT_ONLY_RE.match(cleaned):
        return None
    if len(cleaned) == 1:
        return None
    if BARE_NUMBER_RE.match(cleaned):
        return None
    if cleaned.lower() in GENERIC_ANCHOR_TEXT:
        return None
    return cleaned


def is_raw_url_text(text: str | None) -> bool:
    if text is None:
        return False
    cleaned = text.strip()
    if not cleaned:
        return False
    return bool(URLISH_TEXT_RE.match(cleaned))


def domain_for_display(href: str) -> str | None:
    parsed = urlparse(href)
    host = parsed.netloc.lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def normalize_link_for_compare(href: str) -> str:
    parsed = urlparse(href.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_PARAMS
    ]
    query = urlencode(filtered_query, doseq=True)
    normalized = parsed._replace(
        scheme=scheme,
        netloc=netloc,
        path=path.rstrip("/") or "/",
        query=query,
        fragment="",
    )
    return urlunparse(normalized)


def dedupe_key_for_display(href: str, category: LinkCategory | None = None) -> str:
    parsed = urlparse(href.strip())
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    normalized = normalize_link_for_compare(href)

    if category == "tracking_pixel":
        return "tracking_pixel"
    if category == "junk":
        return normalized
    if host.endswith("substack.com") and path == "/app-link/post":
        publication_id = query_pairs.get("publication_id", "")
        post_id = query_pairs.get("post_id", "")
        if publication_id or post_id:
            return f"substack_post:{publication_id}:{post_id}"
    if host == "calendar.google.com" and path.startswith("/calendar/event"):
        filtered = {
            key: value
            for key, value in query_pairs.items()
            if key.lower() not in TRACKING_QUERY_PARAMS and key.lower() != "rst"
        }
        action = filtered.get("action", "")
        text = filtered.get("text", "")
        dates = filtered.get("dates", "")
        return f"google_calendar:{action}:{text}:{dates}"
    return normalized


def classify_link(
    href: str, text: str | None = None, context: str | None = None
) -> LinkCategory:
    parsed = urlparse(href.strip())
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    cleaned_text = (clean_anchor_text(text) or "").lower()
    context_lc = (context or "").lower()
    combined = " ".join(part for part in (cleaned_text, context_lc) if part)
    is_sendgrid_wrapper = host.endswith("sendgrid.net") and path.startswith("/ls/click")
    is_substack_wrapper = host.endswith("substack.com") and path.startswith("/c/")
    is_known_wrapper = is_sendgrid_wrapper or is_substack_wrapper

    if href.strip().lower() == "http://www.w3.org/1999/xhtml":
        return "junk"
    if path.endswith((".gif", ".png", ".jpg", ".jpeg", ".webp")) and "pixel" in path:
        return "tracking_pixel"
    if ".substackcdn.com" in host and path.endswith(".gif"):
        return "tracking_pixel"
    if (
        "unsubscribe" in combined
        or "manage preferences" in combined
        or "preferences" in path
    ):
        return "unsubscribe_preferences"
    if any(term in combined for term in ("comment", "like", "share")) or any(
        term in path for term in ("/comment", "/comments", "/share", "/like")
    ):
        return "share_comment_like"
    if host == "calendar.google.com":
        return "calendar"
    if "teams.microsoft.com" in host or "events.teams.microsoft.com" in host:
        if "register" in combined or "registration" in combined:
            return "registration"
        return "event"
    if host.endswith("zoom.us"):
        if "/webinar/register" in path or "register" in combined:
            return "registration"
        if path.startswith("/j/"):
            return "event"
    if (
        "youtube.com" in host
        or "youtu.be" in host
        or "vimeo.com" in host
        or "podcast" in host
    ):
        return "video_audio"
    if (
        path.endswith(".pdf")
        or ".pdf" in query
        or " pdf" in f" {combined} "
        or "report" in combined
    ):
        return "document_pdf"
    if (
        "profile" in path
        or "/author/" in path
        or "/authors/" in path
        or "/user/" in path
    ):
        return "author_profile"
    if host.endswith("substack.com") and path == "/app-link/post":
        return "primary_content"
    if "register" in combined or "signup" in combined or "apply here" in combined:
        return "registration"
    if "event" in combined or "event" in path or "webinar" in path:
        return "event"
    if is_known_wrapper:
        if cleaned_text:
            return "content"
        return "unknown"
    if any(term in path for term in ("/article", "/post", "/story", "/news", "/read")):
        return "content"
    if host:
        return "unknown"
    return "junk"


def label_link(
    href: str,
    text: str | None = None,
    category: LinkCategory | None = None,
    context: str | None = None,
) -> str:
    meaningful = clean_anchor_text(text)
    if meaningful:
        return meaningful

    parsed = urlparse(href.strip())
    host = parsed.netloc.lower()
    path = parsed.path
    query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    category = category or classify_link(href, text=text, context=context)

    if host.endswith("substack.com") and path == "/app-link/post":
        return "Open post"
    if host.endswith("substack.com") and "redirect" in path:
        return "Open Substack link"
    if host == "calendar.google.com":
        action = query_pairs.get("action", "").upper()
        if action == "RESPOND":
            return "Respond in Google Calendar"
        if action == "VIEW":
            return "View calendar event"
        return "Open calendar event"
    if host.endswith("zoom.us"):
        if "/webinar/register" in path.lower():
            return "Register for Zoom webinar"
        if path.lower().startswith("/j/"):
            return "Join Zoom"
    if "events.teams.microsoft.com" in host:
        return "Register for Teams event"
    if "youtube.com" in host or "youtu.be" in host:
        return "Watch video"
    if "vimeo.com" in host:
        return "Watch video"
    if category == "document_pdf":
        return "Open PDF"
    if "wikipedia.org" in host and "/wiki/" in path:
        slug = path.split("/wiki/", 1)[1].replace("_", " ").strip()
        return f"Wikipedia: {slug}" if slug else "Open Wikipedia article"
    if category == "author_profile":
        return "Author profile"
    if category == "unsubscribe_preferences":
        return "Manage subscription"
    if category == "registration":
        return "Register"
    if category == "event":
        return "Event page"
    if category == "primary_content":
        return "Read article"
    if category == "content":
        return "Read more"
    domain = domain_for_display(href)
    if domain:
        return f"Open link · {domain}"
    return "Open link"


def classify_links(links: list[LinkItem]) -> list[ClassifiedLink]:
    classified: list[ClassifiedLink] = []
    for item in links:
        category = classify_link(item.href, text=item.text, context=item.context)
        classified.append(
            ClassifiedLink(
                href=item.href,
                text=item.text,
                context=item.context,
                source_index=item.source_index,
                label=label_link(
                    item.href, text=item.text, category=category, context=item.context
                ),
                domain=domain_for_display(item.href),
                category=category,
                priority=PRIORITY_BY_CATEGORY[category],
                is_main=False,
                hidden_reason=None,
                dedupe_key=dedupe_key_for_display(item.href, category=category),
            )
        )
    return classified


def prepare_links_for_render(
    links: list[LinkItem], max_main: int = 5, max_other: int = 10
) -> LinkRenderBundle:
    classified = classify_links(links)
    main_links: list[ClassifiedLink] = []
    other_links: list[ClassifiedLink] = []
    hidden_links: list[ClassifiedLink] = []
    seen_display_keys: set[str] = set()

    for link in sorted(classified, key=lambda item: (item.priority, item.source_index)):
        if link.category in HIDDEN_CATEGORIES:
            hidden_links.append(
                ClassifiedLink(
                    **{
                        **link.__dict__,
                        "hidden_reason": link.category,
                        "is_main": False,
                    }
                )
            )
            continue

        if link.dedupe_key in seen_display_keys:
            hidden_links.append(
                ClassifiedLink(
                    **{
                        **link.__dict__,
                        "hidden_reason": "duplicate_for_display",
                        "is_main": False,
                    }
                )
            )
            continue

        seen_display_keys.add(link.dedupe_key)
        if link.category in PRIMARY_CATEGORIES and len(main_links) < max_main:
            main_links.append(ClassifiedLink(**{**link.__dict__, "is_main": True}))
            continue
        if len(other_links) < max_other:
            other_links.append(ClassifiedLink(**{**link.__dict__, "is_main": False}))
            continue
        hidden_links.append(
            ClassifiedLink(
                **{**link.__dict__, "hidden_reason": "over_limit", "is_main": False}
            )
        )

    return LinkRenderBundle(
        main_links=tuple(main_links),
        other_links=tuple(other_links),
        hidden_links=tuple(hidden_links),
    )


def render_link_markdown(link: ClassifiedLink) -> str:
    label = link.label.replace("\\", "\\\\").replace("[", r"\[").replace("]", r"\]")
    return f"- [{label}]({link.href})"


def render_link_html(link: ClassifiedLink) -> str:
    href = html.escape(link.href, quote=True)
    label = html.escape(link.label)
    domain = html.escape(link.domain) if link.domain else None
    if domain and not link.label.endswith(domain):
        return f'<li><a href="{href}" rel="noopener">{label}</a> <span class="link-domain">· {domain}</span></li>'
    return f'<li><a href="{href}" rel="noopener">{label}</a></li>'

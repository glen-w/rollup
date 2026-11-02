"""Primary navigation model for the web shell (grouped sidebar)."""

from __future__ import annotations

from flask import request, url_for

# (key, label, endpoint)
NAV_SECTIONS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "Briefings",
        (
            ("home", "Home", "home.home_index"),
            ("archive", "Archive", "rollups.list_rollups"),
            ("search", "Search", "search.search_index"),
        ),
    ),
    (
        "Sources",
        (
            ("sources", "Sources", "sources.list_sources"),
            ("articles", "Articles", "articles.articles_index"),
            ("linkedin", "LinkedIn", "linkedin.linkedin_index"),
            ("reddit", "Reddit", "reddit.reddit_index"),
            ("rss", "RSS", "rss.rss_index"),
        ),
    ),
    (
        "Operate",
        (
            ("run", "Run Studio", "run.run_studio"),
            ("settings", "Settings", "settings.settings_index"),
            ("admin", "Admin", "admin.admin_index"),
        ),
    ),
)

# Blueprint name → nav key (blueprints without their own entry map to a parent).
_BLUEPRINT_TO_KEY = {
    "home": "home",
    "rollups": "archive",
    "messages": "archive",
    "artifacts": "archive",
    "search": "search",
    "sources": "sources",
    "articles": "articles",
    "linkedin": "linkedin",
    "reddit": "reddit",
    "rss": "rss",
    "run": "run",
    "settings": "settings",
    "admin": "admin",
}


def active_nav_key() -> str | None:
    endpoint = request.endpoint or ""
    blueprint = endpoint.split(".", 1)[0] if "." in endpoint else endpoint
    return _BLUEPRINT_TO_KEY.get(blueprint)


def nav_sections() -> list[dict]:
    sections: list[dict] = []
    for title, items in NAV_SECTIONS:
        sections.append(
            {
                "title": title,
                "links": [
                    {"key": key, "label": label, "url": url_for(endpoint)}
                    for key, label, endpoint in items
                ],
            }
        )
    return sections

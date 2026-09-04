"""Detect Google Scholar alert emails vs synthetic paper messages."""

from __future__ import annotations

from rollup.source_identity import normalize_email

PAPER_MESSAGE_KEY_PREFIX = "scholar:paper:"

SCHOLAR_FROM_ADDRS = frozenset(
    {
        "scholaralerts-noreply@google.com",
        "scholaralerts@google.com",
    }
)
SCHOLAR_LIST_ID_MARKERS = ("scholar-alerts.google.com",)
SCHOLAR_SUBJECT_PREFIX = "scholar alert:"


def is_scholar_paper_key(message_key: str | None) -> bool:
    return bool(message_key) and message_key.startswith(PAPER_MESSAGE_KEY_PREFIX)


def is_scholar_source_key(source_key: str | None, list_id: str | None = None) -> bool:
    key = source_key or ""
    if key in (
        "from:scholaralerts-noreply@google.com",
        "from:scholaralerts@google.com",
        "list:scholar-alerts.google.com",
    ):
        return True
    lid = (list_id or "").lower()
    return any(marker in lid for marker in SCHOLAR_LIST_ID_MARKERS)


def is_scholar_alert(parsed: object) -> bool:
    """True for Scholar *alert emails*, not synthetic per-paper messages."""
    message_key = getattr(parsed, "message_key", None)
    if is_scholar_paper_key(message_key):
        return False

    sender = getattr(parsed, "sender", None) or ""
    addr = normalize_email(str(sender))
    if addr in SCHOLAR_FROM_ADDRS:
        return True

    source_key = getattr(parsed, "source_key", None) or ""
    list_id = (getattr(parsed, "list_id", None) or "").lower()
    if is_scholar_source_key(source_key, list_id):
        return True

    subject = (getattr(parsed, "subject", None) or "").strip().lower()
    return subject.startswith(SCHOLAR_SUBJECT_PREFIX)

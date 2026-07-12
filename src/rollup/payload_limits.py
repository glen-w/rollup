"""Central payload size limits for indexing and web requests."""

from __future__ import annotations

MAX_SUBJECT_LEN = 2000
MAX_SENDER_LEN = 1000
MAX_SUMMARY_LEN = 50_000
MAX_FOLDER_NAME_LEN = 500
MAX_DISPLAY_NAME_LEN = 500
MAX_DATE_RAW_LEN = 200
MAX_LINK_ITEMS = 20
MAX_LINK_HREF_LEN = 2000
MAX_LINK_LABEL_LEN = 500
MAX_REASON_CODES = 12
MAX_RELPATH_LEN = 1000
LINKS_JSON_VERSION = 1
ENTRY_INDEX_VERSION = 1
REPORT_SCHEMA_VERSION = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_RECENT_SOURCE_EMAILS = 50


def clip_text(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) > max_len:
        return text[:max_len]
    return text

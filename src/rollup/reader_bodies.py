"""Reader body types, clipping, hashing, and text preparation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from rollup.payload_limits import MAX_READER_BODY_LEN
from rollup.web_ids import validate_message_key

READER_BODY_INDEX_VERSION = 1
READER_TEXT_VERSION = 1

# Exact html2text empty-image placeholders (fixed list; version bump if changed).
_HTML2TEXT_EMPTY_IMAGE_PLACEHOLDERS = frozenset({"![]", "![]( )"})

_DISALLOWED_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UNICODE_LINE_SEP = re.compile(r"[\u2028\u2029]")
_MAX_BLANK_LINES = 2


class ReaderBodyError(ValueError):
    """Invalid reader body payload."""


@dataclass(frozen=True)
class ReaderBodyWrite:
    message_key: str
    content_hash: str
    body_text: str
    truncated: bool
    stored_body_hash: str

    def __repr__(self) -> str:
        return (
            f"ReaderBodyWrite(message_key={self.message_key!r}, "
            f"content_hash={self.content_hash!r}, truncated={self.truncated}, "
            f"stored_body_hash={self.stored_body_hash!r}, body_len={len(self.body_text)})"
        )


@dataclass(frozen=True)
class ReaderBodyRecord:
    message_key: str
    content_hash: str
    stored_body_hash: str
    body_text: str
    truncated: bool
    updated_at: str
    last_seen_at: str
    reader_text_version: int = 0
    source_body_length: int = -1
    reader_content_hash: str | None = None
    reader_hash_authoritative: bool = False
    first_indexed_at: str | None = None

    def __repr__(self) -> str:
        return (
            f"ReaderBodyRecord(message_key={self.message_key!r}, "
            f"truncated={self.truncated}, body_len={len(self.body_text)})"
        )


@dataclass(frozen=True)
class BodyUpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0
    identical_duplicates: int = 0


@dataclass(frozen=True)
class PreparedReaderText:
    text: str
    reader_text_version: int
    source_body_length: int
    truncated: bool


def _validate_hash_hex(value: str, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ReaderBodyError(f"invalid {field} length")
    if value != value.lower() or any(c not in "0123456789abcdef" for c in value):
        raise ReaderBodyError(f"invalid {field} format")
    return value


def _reject_nul(text: str) -> None:
    if "\x00" in text:
        raise ReaderBodyError("body text contains NUL")


def _reject_surrogates(text: str) -> None:
    for ch in text:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise ReaderBodyError("body text contains lone surrogate")


def compute_stored_body_hash(*, truncated: bool, body_text: str) -> str:
    """SHA-256 of v1 length-delimited (truncated flag + utf8 body)."""
    utf8 = body_text.encode("utf-8")
    payload = (
        b"v1\x00"
        + bytes([1 if truncated else 0])
        + b"\x00"
        + len(utf8).to_bytes(4, "big")
        + utf8
    )
    return hashlib.sha256(payload).hexdigest()


def compute_reader_content_hash(*, reader_text_version: int, prepared_text: str) -> str:
    """SHA-256 fingerprint of prepared text before reader cap."""
    utf8 = prepared_text.encode("utf-8")
    payload = (
        b"rt1\x00"
        + int(reader_text_version).to_bytes(4, "big")
        + len(utf8).to_bytes(4, "big")
        + utf8
    )
    return hashlib.sha256(payload).hexdigest()


def clip_reader_text(source_text: str) -> tuple[str, bool]:
    if len(source_text) > MAX_READER_BODY_LEN:
        return source_text[:MAX_READER_BODY_LEN], True
    return source_text, False


def make_reader_body_write(
    message_key: str,
    content_hash: str,
    source_text: str,
) -> ReaderBodyWrite:
    """Trusted factory: clip, hash, validate."""
    key = validate_message_key(message_key)
    ch = _validate_hash_hex(content_hash, field="content_hash")
    if not source_text:
        source_text = ""
    _reject_nul(source_text)
    _reject_surrogates(source_text)
    body_text, truncated = clip_reader_text(source_text)
    stored = compute_stored_body_hash(truncated=truncated, body_text=body_text)
    return ReaderBodyWrite(
        message_key=key,
        content_hash=ch,
        body_text=body_text,
        truncated=truncated,
        stored_body_hash=stored,
    )


def validate_reader_body_write(write: ReaderBodyWrite) -> None:
    """Revalidate at transaction boundary."""
    expected = make_reader_body_write(
        write.message_key, write.content_hash, write.body_text
    )
    if expected.truncated != write.truncated or expected.stored_body_hash != write.stored_body_hash:
        raise ReaderBodyError("reader body write invariant mismatch")
    if len(write.body_text) > MAX_READER_BODY_LEN:
        raise ReaderBodyError("body exceeds cap")
    if write.truncated and len(write.body_text) != MAX_READER_BODY_LEN:
        raise ReaderBodyError("truncated flag inconsistent with length")


def prepare_reader_text(body_text: str) -> PreparedReaderText:
    """Deterministic reader-text normalisation (READER_TEXT_VERSION=1)."""
    text = body_text.replace("\r\n", "\n").replace("\r", "\n")
    text = _UNICODE_LINE_SEP.sub("\n", text)
    text = _DISALLOWED_CONTROLS.sub("", text)
    lines = [line.rstrip(" \t") for line in text.split("\n")]
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in _HTML2TEXT_EMPTY_IMAGE_PLACEHOLDERS or stripped == "![]()":
            continue
        cleaned_lines.append(line)
    collapsed: list[str] = []
    blank_run = 0
    for line in cleaned_lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= _MAX_BLANK_LINES:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(line)
    while collapsed and not collapsed[0].strip():
        collapsed.pop(0)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    result = "\n".join(collapsed)
    source_len = len(result)
    clipped, truncated = clip_reader_text(result)
    return PreparedReaderText(
        text=clipped,
        reader_text_version=READER_TEXT_VERSION,
        source_body_length=source_len,
        truncated=truncated,
    )


def build_reader_body_writes_from_entries(
    report_entries: list,
) -> tuple[list[ReaderBodyWrite], int, int]:
    """Build writes from digest entries; returns (writes, identical_dupes, conflicts)."""
    from rollup.models import DigestEntry, DigestGroup, DigestItem

    winners: dict[str, ReaderBodyWrite] = {}
    identical = 0
    conflicts = 0

    def _add(entry: DigestEntry) -> None:
        nonlocal identical, conflicts
        parsed = entry.classified.parsed
        if not parsed.content_hash:
            raise ReaderBodyError("missing content_hash")
        candidate = make_reader_body_write(
            parsed.message_key,
            parsed.content_hash,
            parsed.body_text,
        )
        existing = winners.get(candidate.message_key)
        if existing is None:
            winners[candidate.message_key] = candidate
            return
        if (
            existing.content_hash == candidate.content_hash
            and existing.stored_body_hash == candidate.stored_body_hash
            and existing.body_text == candidate.body_text
            and existing.truncated == candidate.truncated
        ):
            identical += 1
            return
        conflicts += 1

    sections: list[tuple[str, list]] = []
    # Caller passes flattened digest items via report walk
    for item in report_entries:
        if isinstance(item, DigestGroup):
            for entry in item.entries:
                _add(entry)
        elif isinstance(item, DigestEntry):
            _add(item)

    return list(winners.values()), identical, conflicts


def collect_digest_items(report) -> list:
    """Flatten report to digest items in index order."""
    items: list = []
    for folder_items in report.dated_by_folder.values():
        items.extend(folder_items)
    items.extend(report.undated)
    return items


def build_reader_writes_for_report(report) -> tuple[list[ReaderBodyWrite], int, int]:
    """Build body writes matching flatten_report_entries winner order."""
    by_key: dict[str, ReaderBodyWrite] = {}
    identical = 0
    conflicts = 0
    from rollup.models import DigestEntry, DigestGroup

    sections: list = list(report.dated_by_folder.items())
    if report.undated:
        sections.append(("undated", report.undated))

    for _section_key, section_items in sections:
        for item in section_items:
            entries: list[DigestEntry]
            if isinstance(item, DigestGroup):
                entries = list(item.entries)
            else:
                entries = [item]
            for entry in entries:
                parsed = entry.classified.parsed
                if not parsed.content_hash:
                    raise ReaderBodyError("missing content_hash")
                candidate = make_reader_body_write(
                    parsed.message_key,
                    parsed.content_hash,
                    parsed.body_text,
                )
                existing = by_key.get(candidate.message_key)
                if existing is None:
                    by_key[candidate.message_key] = candidate
                    continue
                if (
                    existing.content_hash == candidate.content_hash
                    and existing.stored_body_hash == candidate.stored_body_hash
                    and existing.body_text == candidate.body_text
                    and existing.truncated == candidate.truncated
                ):
                    identical += 1
                    continue
                conflicts += 1

    if conflicts:
        raise ReaderBodyError(
            f"conflicting duplicate message keys in index payload ({conflicts})"
        )
    return list(by_key.values()), identical, conflicts

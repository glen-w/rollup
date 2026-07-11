"""Conservative deterministic grouping for notification streams and daily editions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from email.utils import parseaddr
from statistics import median
from typing import Literal

from rollup.models import DigestEntry, DigestGroup, DigestItem, GroupType
from rollup.run_options import GroupingConfig

ReasonCode = Literal[
    "LONG_FORM_STANDALONE",
    "BELOW_MIN_SIZE",
    "TYPE_MISMATCH",
    "SUBJECT_FAMILY_MISMATCH",
    "FORMED_NOTIFICATION_STREAM",
    "FORMED_DAILY_EDITIONS",
]

NOTIFICATION_TYPES = frozenset({"short_update", "unclassified"})
EDITION_SUBJECT_RE = re.compile(
    r"\b("
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|issue\s*#?\d+"
    r"|edition"
    r"|daily"
    r")\b",
    re.IGNORECASE,
)
SUBJECT_NOISE_RE = re.compile(
    r"^(re|fwd|fw)\s*:\s*",
    re.IGNORECASE,
)
DATE_IN_SUBJECT_RE = re.compile(
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"
    r"|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?"
    r"|dec(?:ember)?)\s+\d{1,2}(?:,?\s*\d{4})?\b"
    r"|\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?\b"
    r"|\bissue\s*#?\d+\b",
    re.IGNORECASE,
)
MAX_GROUP_SIZE = 15


@dataclass(frozen=True)
class GroupingDecision:
    reason_code: ReasonCode
    message_key: str | None = None
    group_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class GroupingApplyResult:
    """Result of apply_grouping (kept out of pipeline to avoid circular imports)."""

    dated_items: tuple[DigestItem, ...]
    undated_items: tuple[DigestItem, ...]
    groups: tuple[DigestGroup, ...] = ()
    reason_codes: tuple[GroupingDecision, ...] = ()


def normalize_email(from_header: str) -> str:
    """Lowercase email address from a From header."""
    _, addr = parseaddr(from_header or "")
    addr = (addr or from_header or "").strip().lower()
    if "<" in addr and ">" in addr:
        addr = addr[addr.find("<") + 1 : addr.find(">")].strip()
    return addr


def normalize_subject_family(subject: str) -> str:
    """Strip Re/Fwd, dates, and issue numbers for exact family matching."""
    text = (subject or "").strip()
    while True:
        cleaned = SUBJECT_NOISE_RE.sub("", text).strip()
        if cleaned == text:
            break
        text = cleaned
    text = DATE_IN_SUBJECT_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def is_long_form_standalone(entry: DigestEntry) -> bool:
    """Single canonical long-form exclusion predicate."""
    parsed = entry.classified.parsed
    return (
        entry.classified.newsletter_type == "essay"
        or len(parsed.body_text.split()) >= 1000
    )


def _word_count(entry: DigestEntry) -> int:
    return len(entry.classified.parsed.body_text.split())


def _group_id(group_type: GroupType, sender: str, folder: str, family: str) -> str:
    raw = f"{group_type}|{sender}|{folder}|{family}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _display_name(sender: str, subject_family: str) -> str:
    local = sender.split("@", 1)[0] if "@" in sender else sender
    if subject_family:
        return f"{local} · {subject_family[:40]}"
    return local or sender


def apply_grouping(
    dated_entries: tuple[DigestEntry, ...],
    undated_entries: tuple[DigestEntry, ...],
    config: GroupingConfig,
) -> GroupingApplyResult:
    """Group dated entries; undated remain standalone in v1."""
    if not config.enabled:
        return GroupingApplyResult(
            dated_items=dated_entries, undated_items=undated_entries
        )

    decisions: list[GroupingDecision] = []
    dated_items, groups, dated_decisions = _group_entry_list(
        list(dated_entries), config
    )
    decisions.extend(dated_decisions)

    # Undated: no grouping in v1.
    for entry in undated_entries:
        if is_long_form_standalone(entry):
            decisions.append(
                GroupingDecision(
                    reason_code="LONG_FORM_STANDALONE",
                    message_key=entry.classified.parsed.message_key,
                )
            )

    return GroupingApplyResult(
        dated_items=tuple(dated_items),
        undated_items=undated_entries,
        groups=tuple(groups),
        reason_codes=tuple(decisions),
    )


def _group_entry_list(
    entries: list[DigestEntry],
    config: GroupingConfig,
) -> tuple[list[DigestItem], list[DigestGroup], list[GroupingDecision]]:
    decisions: list[GroupingDecision] = []
    standalone: list[DigestEntry] = []
    candidates: list[DigestEntry] = []

    for entry in entries:
        if is_long_form_standalone(entry):
            decisions.append(
                GroupingDecision(
                    reason_code="LONG_FORM_STANDALONE",
                    message_key=entry.classified.parsed.message_key,
                )
            )
            standalone.append(entry)
        else:
            candidates.append(entry)

    # Bucket by (sender, folder).
    buckets: dict[tuple[str, str], list[DigestEntry]] = {}
    for entry in candidates:
        sender = normalize_email(entry.classified.parsed.sender)
        folder = entry.classified.parsed.folder_name
        buckets.setdefault((sender, folder), []).append(entry)

    items: list[DigestItem] = list(standalone)
    groups: list[DigestGroup] = []
    grouped_keys: set[str] = set()

    for (sender, folder), bucket in sorted(buckets.items()):
        if len(bucket) < config.min_group_size:
            for entry in bucket:
                decisions.append(
                    GroupingDecision(
                        reason_code="BELOW_MIN_SIZE",
                        message_key=entry.classified.parsed.message_key,
                        detail=f"size={len(bucket)}",
                    )
                )
                items.append(entry)
            continue

        # Try daily_editions first (stricter subject pattern).
        daily = _try_daily_editions(bucket, sender, folder, decisions)
        if daily is not None:
            for chunk in _chunk(daily, MAX_GROUP_SIZE):
                group = _make_group("daily_editions", sender, folder, chunk, "expandable")
                groups.append(group)
                items.append(group)
                grouped_keys.update(e.classified.parsed.message_key for e in chunk)
                decisions.append(
                    GroupingDecision(
                        reason_code="FORMED_DAILY_EDITIONS",
                        group_id=group.group_id,
                        detail=f"n={len(chunk)}",
                    )
                )
            continue

        notif = _try_notification_stream(bucket, config, decisions)
        if notif is not None:
            for chunk in _chunk(notif, MAX_GROUP_SIZE):
                group = _make_group(
                    "notification_stream", sender, folder, chunk, "compact"
                )
                groups.append(group)
                items.append(group)
                grouped_keys.update(e.classified.parsed.message_key for e in chunk)
                decisions.append(
                    GroupingDecision(
                        reason_code="FORMED_NOTIFICATION_STREAM",
                        group_id=group.group_id,
                        detail=f"n={len(chunk)}",
                    )
                )
            continue

        for entry in bucket:
            items.append(entry)

    # Preserve approximate newest-first order by first entry date when possible.
    def sort_key(item: DigestItem) -> tuple:
        if isinstance(item, DigestGroup):
            dates = [
                e.classified.parsed.date_parsed.timestamp()
                for e in item.entries
                if e.classified.parsed.date_parsed
            ]
            return (-max(dates) if dates else 0, item.display_name.lower())
        parsed = item.classified.parsed
        ts = parsed.date_parsed.timestamp() if parsed.date_parsed else 0
        return (-ts, parsed.subject.lower())

    items.sort(key=sort_key)
    return items, groups, decisions


def _try_daily_editions(
    bucket: list[DigestEntry],
    sender: str,
    folder: str,
    decisions: list[GroupingDecision],
) -> list[DigestEntry] | None:
    if len(bucket) < 4:
        return None
    families = {normalize_subject_family(e.classified.parsed.subject) for e in bucket}
    if len(families) != 1:
        for entry in bucket:
            decisions.append(
                GroupingDecision(
                    reason_code="SUBJECT_FAMILY_MISMATCH",
                    message_key=entry.classified.parsed.message_key,
                )
            )
        return None
    if not all(
        EDITION_SUBJECT_RE.search(e.classified.parsed.subject or "") for e in bucket
    ):
        return None
    if any(is_long_form_standalone(e) for e in bucket):
        return None
    return bucket


def _try_notification_stream(
    bucket: list[DigestEntry],
    config: GroupingConfig,
    decisions: list[GroupingDecision],
) -> list[DigestEntry] | None:
    if len(bucket) < config.min_group_size:
        return None
    typed = []
    for entry in bucket:
        if entry.classified.newsletter_type not in NOTIFICATION_TYPES:
            decisions.append(
                GroupingDecision(
                    reason_code="TYPE_MISMATCH",
                    message_key=entry.classified.parsed.message_key,
                    detail=entry.classified.newsletter_type,
                )
            )
            return None
        typed.append(entry)
    words = [_word_count(e) for e in typed]
    if not words or median(words) >= 150:
        return None
    return typed


def _make_group(
    group_type: GroupType,
    sender: str,
    folder: str,
    entries: list[DigestEntry],
    render_mode: str,
) -> DigestGroup:
    family = normalize_subject_family(entries[0].classified.parsed.subject)
    return DigestGroup(
        group_id=_group_id(group_type, sender, folder, family),
        group_type=group_type,
        display_name=_display_name(sender, family),
        sender_normalized=sender,
        folder_name=folder,
        entries=tuple(entries),
        render_mode=render_mode,  # type: ignore[arg-type]
    )


def _chunk(entries: list[DigestEntry], size: int) -> list[list[DigestEntry]]:
    return [entries[i : i + size] for i in range(0, len(entries), size)]


def build_grouping_report(result: GroupingApplyResult) -> str:
    lines = [
        f"Groups created: {len(result.groups)}",
        f"Dated items: {len(result.dated_items)}",
        f"Undated items: {len(result.undated_items)}",
    ]
    for decision in result.reason_codes:
        lines.append(
            f"  {decision.reason_code}"
            + (f" key={decision.message_key}" if decision.message_key else "")
            + (f" group={decision.group_id}" if decision.group_id else "")
            + (f" {decision.detail}" if decision.detail else "")
        )
    return "\n".join(lines)

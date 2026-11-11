"""Canonical UTC helper contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rollup.utc import format_utc, now_utc, parse_utc


def test_format_utc_second_resolution_z() -> None:
    dt = datetime(2026, 11, 5, 12, 34, 56, 789000, tzinfo=timezone.utc)
    assert format_utc(dt) == "2026-11-05T12:34:56Z"


def test_format_utc_naive_treated_as_utc() -> None:
    dt = datetime(2026, 1, 2, 3, 4, 5)
    assert format_utc(dt) == "2026-01-02T03:04:05Z"


def test_format_utc_converts_offset() -> None:
    dt = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert format_utc(dt) == "2026-06-01T12:00:00Z"


def test_parse_utc_z_and_offset() -> None:
    z = parse_utc("2026-11-05T12:34:56Z")
    assert z is not None
    assert z.tzinfo is not None
    assert z == datetime(2026, 11, 5, 12, 34, 56, tzinfo=timezone.utc)

    offset = parse_utc("2026-11-05T14:34:56+02:00")
    assert offset == z


def test_parse_utc_rejects_empty_and_garbage() -> None:
    assert parse_utc(None) is None
    assert parse_utc("") is None
    assert parse_utc("   ") is None
    assert parse_utc("not-a-date") is None


def test_parse_utc_naive_iso_assumes_utc() -> None:
    dt = parse_utc("2026-03-01T08:00:00")
    assert dt == datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)


def test_now_utc_is_aware() -> None:
    n = now_utc()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(0)

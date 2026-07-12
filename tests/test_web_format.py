"""Tests for human-friendly web date formatting."""

from __future__ import annotations

from datetime import datetime, timezone

from rollup.web.format import format_human_date_range, format_human_datetime


def test_format_human_datetime() -> None:
    assert (
        format_human_datetime("2026-07-12T23:57:22.422511+02:00")
        == "Sunday 12 July 2026, 23:57"
    )


def test_format_human_datetime_none() -> None:
    assert format_human_datetime(None) == "—"
    assert format_human_datetime("") == "—"


def test_format_human_date_range_cross_month() -> None:
    assert (
        format_human_date_range(
            "2026-06-29T00:00:00+02:00",
            "2026-07-12T23:59:59.999999+02:00",
        )
        == "Monday 29 June – Sunday 12 July, 2026"
    )


def test_format_human_date_range_same_month() -> None:
    assert (
        format_human_date_range(
            "2026-07-23T00:00:00Z",
            "2026-07-30T23:59:59Z",
        )
        == "Thursday 23 – Thursday 30 July, 2026"
    )


def test_format_human_date_range_same_day() -> None:
    assert (
        format_human_date_range(
            "2024-06-01T00:00:00Z",
            "2024-06-01T23:59:59Z",
        )
        == "Saturday 1 June, 2024"
    )


def test_format_human_date_range_cross_year() -> None:
    assert (
        format_human_date_range(
            datetime(2025, 12, 28, tzinfo=timezone.utc),
            datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        == "Sunday 28 December 2025 – Saturday 3 January, 2026"
    )

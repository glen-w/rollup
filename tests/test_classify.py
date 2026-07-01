"""Tests for newsletter classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.classify import classify_message
from rollup.discovery import iter_mbox_files
from rollup.parse import parse_mbox_folder

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
CLASSIFY_ROOT = FIXTURE_ROOT / "classify.sbd"


def _parse_folder(name: str):
    path = CLASSIFY_ROOT / name
    from rollup.models import MboxFolder

    folder = MboxFolder(
        folder_name=f"classify/{name}",
        relative_path=f"classify/{name}",
        mbox_path=path,
        size_bytes=path.stat().st_size,
    )
    msgs, _, errs = parse_mbox_folder(folder, 200_000, 8)
    assert msgs, f"No messages in {name}: {errs}"
    return classify_message(msgs[0])


def test_classify_short_update() -> None:
    result = _parse_folder("short_update")
    assert result.newsletter_type == "short_update"


def test_classify_essay() -> None:
    result = _parse_folder("essay")
    assert result.newsletter_type == "essay"


def test_classify_link_roundup() -> None:
    result = _parse_folder("link_roundup")
    assert result.newsletter_type == "link_roundup"


def test_classify_multi_section_digest() -> None:
    result = _parse_folder("multi_section_digest")
    assert result.newsletter_type == "multi_section_digest"


def test_classify_unclassified_empty() -> None:
    path = CLASSIFY_ROOT / "unclassified_empty"
    from rollup.models import MboxFolder

    folder = MboxFolder(
        folder_name="classify/unclassified_empty",
        relative_path="classify/unclassified_empty",
        mbox_path=path,
        size_bytes=0,
    )
    msgs, _, _ = parse_mbox_folder(folder, 200_000, 8)
    if not msgs:
        pytest.skip("empty mbox has no messages to classify")
    result = classify_message(msgs[0])
    assert result.newsletter_type == "unclassified"


def test_classification_scores_immutable() -> None:
    result = _parse_folder("short_update")
    assert isinstance(result.classification_scores, tuple)

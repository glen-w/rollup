"""Synthetic inconsistent digest fixtures for final review."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from rollup.final_review import build_review_corpus, parse_final_review_response
from rollup.models import DigestEntry
from test_final_review import _entry, _report


def _mock_response_for_issue(issue_type: str) -> str:
    return json.dumps(
        {
            "overall_status": "pass_with_warnings",
            "safe_to_publish": True,
            "issues": [
                {
                    "severity": "minor",
                    "type": issue_type,
                    "location": "fixture",
                    "entry_id": "k1",
                    "description": f"Detected {issue_type}",
                    "suggested_fix": None,
                    "safe_auto_fix": False,
                }
            ],
            "patches": [],
        }
    )


@pytest.mark.parametrize(
    ("fixture_name", "entries", "expected_type"),
    [
        (
            "mixed_bullet_styles",
            {
                "tech": (
                    _entry(message_key="k1", summary="- alpha"),
                    _entry(message_key="k2", summary="* beta"),
                    _entry(message_key="k3", summary="1. gamma"),
                )
            },
            "style_drift",
        ),
        (
            "duplicate_theme",
            {
                "tech": (
                    _entry(
                        message_key="k1",
                        summary="- Company X announced a major product launch today.",
                    ),
                    _entry(
                        message_key="k2",
                        summary="- Company X announced a major product launch today with details.",
                    ),
                )
            },
            "duplication",
        ),
        (
            "date_wording_drift",
            {
                "tech": (
                    _entry(message_key="k1", summary="- Event happened last Tuesday."),
                    _entry(message_key="k2", summary="- Event on 2026-06-24."),
                )
            },
            "date_inconsistency",
        ),
        (
            "heading_mismatch",
            {
                "tech": (
                    _entry(message_key="k1", summary="## Section\n- point"),
                    _entry(message_key="k2", summary="- plain bullets only"),
                )
            },
            "heading_inconsistency",
        ),
        (
            "metadata_mismatch",
            {
                "tech": (
                    _entry(
                        message_key="k1",
                        summary="- Long essay-style analysis with nuance and caveats.",
                    ),
                )
            },
            "metadata_mismatch",
        ),
        (
            "synthetic_contradiction",
            {
                "tech": (
                    _entry(message_key="k1", summary="- Revenue grew 20%."),
                    _entry(message_key="k2", summary="- Revenue fell 20%."),
                )
            },
            "possible_contradiction",
        ),
        (
            "length_outlier",
            {
                "tech": (
                    _entry(message_key="k1", summary="- short"),
                    _entry(
                        message_key="k2",
                        summary="\n".join(f"- bullet {i}" for i in range(15)),
                    ),
                )
            },
            "length_mismatch",
        ),
    ],
)
def test_fixture_corpus_builds_and_mock_parse(
    fixture_name: str,
    entries: dict[str, tuple[DigestEntry, ...]],
    expected_type: str,
) -> None:
    report = _report(entries_by_folder=entries)
    corpus = build_review_corpus(report)
    assert corpus.entry_count >= 1

    result = parse_final_review_response(
        _mock_response_for_issue(expected_type),
        profile_name="strict",
        model="qwen2.5:7b",
        generated_at=datetime.now().astimezone(),
        digest_fingerprint="fp",
        review_input_hash="ih",
    )
    assert result.issues[0].type == expected_type

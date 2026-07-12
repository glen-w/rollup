"""Adversarial / fail-closed parsing for final-review structured output."""

from __future__ import annotations

from datetime import datetime, timezone

from rollup.final_review import (
    _parse_issue,
    _result_from_cached_json,
    parse_final_review_response,
)


def test_safe_auto_fix_string_true_is_false() -> None:
    issue = _parse_issue(
        {
            "severity": "minor",
            "type": "other",
            "location": "x",
            "description": "d",
            "safe_auto_fix": "true",
            "issue_id": "i1",
        }
    )
    assert issue is not None
    assert issue.safe_auto_fix is False


def test_safe_auto_fix_null_is_false() -> None:
    issue = _parse_issue(
        {
            "severity": "minor",
            "type": "other",
            "location": "x",
            "description": "d",
            "safe_auto_fix": None,
            "issue_id": "i1",
        }
    )
    assert issue is not None
    assert issue.safe_auto_fix is False


def test_safe_to_publish_coercion_fail_closed() -> None:
    result = parse_final_review_response(
        '{"overall_status":"pass","safe_to_publish":"yes","issues":[],"patches":[]}',
        profile_name="strict",
        model="m",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint="fp",
        review_input_hash="ih",
    )
    assert result.safe_to_publish is False


def test_null_patches_array() -> None:
    result = parse_final_review_response(
        '{"overall_status":"pass","safe_to_publish":true,"issues":[],"patches":null}',
        profile_name="strict",
        model="m",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint="fp",
        review_input_hash="ih",
    )
    assert result.patches == ()


def test_oversized_issue_id_dropped() -> None:
    issue = _parse_issue(
        {
            "severity": "minor",
            "type": "other",
            "location": "x",
            "description": "d",
            "safe_auto_fix": True,
            "issue_id": "x" * 300,
        }
    )
    assert issue is not None
    assert issue.issue_id is None


def test_unknown_fields_ignored() -> None:
    result = parse_final_review_response(
        '{"overall_status":"pass","safe_to_publish":true,"issues":[],'
        '"patches":[],"extra_field":123,"digest_fingerprint":"fp"}',
        profile_name="strict",
        model="m",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint="host-fp",
        review_input_hash="ih",
    )
    assert result.echoed_digest_fingerprint == "fp"
    assert result.digest_fingerprint == "host-fp"


def test_cached_missing_echo_not_synthesised() -> None:
    payload = (
        '{"overall_status":"pass","safe_to_publish":true,"issues":[],"patches":[],'
        '"digest_fingerprint":"host-fp","review_input_hash":"ih",'
        '"profile_name":"strict","model":"m","prompt_version":"v",'
        '"generated_at":"2026-07-12T00:00:00+00:00","review_mode":"apply"}'
    )
    result = _result_from_cached_json(
        payload,
        profile_name="strict",
        model="m",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint="host-fp",
        review_input_hash="ih",
    )
    assert result.echoed_digest_fingerprint is None
    assert result.digest_fingerprint == "host-fp"


def test_cached_mismatched_echo_preserved() -> None:
    payload = (
        '{"overall_status":"pass","safe_to_publish":true,"issues":[],"patches":[],'
        '"digest_fingerprint":"host-fp","echoed_digest_fingerprint":"old-echo",'
        '"review_input_hash":"ih","profile_name":"strict","model":"m",'
        '"prompt_version":"v","generated_at":"2026-07-12T00:00:00+00:00",'
        '"review_mode":"apply"}'
    )
    result = _result_from_cached_json(
        payload,
        profile_name="strict",
        model="m",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint="host-fp",
        review_input_hash="ih",
    )
    assert result.echoed_digest_fingerprint == "old-echo"


def test_malformed_cached_payload_error_source() -> None:
    result = _result_from_cached_json(
        '"not-an-object"',
        profile_name="strict",
        model="m",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint="fp",
        review_input_hash="ih",
    )
    assert result.review_source == "error"

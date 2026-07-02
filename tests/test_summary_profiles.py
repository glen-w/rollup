"""Tests for summary profile config and planning."""

from __future__ import annotations

from rollup.filter import make_digest_entry
from rollup.models import ClassifiedMessage, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.summary_plan import SummaryCliOptions, resolve_summary_plan
from rollup.summary_profiles import (
    get_builtin_summary_profile_set,
    get_canonical_newsletter_types,
    list_summary_profiles,
    list_type_routes,
    require_valid_summary_profile_set,
    summary_profile_set_from_dict,
    summary_profile_set_to_dict,
    validate_summary_profile_set,
)


def _entry(newsletter_type: str = "short_update"):
    parsed = ParsedMessage(
        message_key=f"key-{newsletter_type}",
        content_hash=compute_content_hash(f"body-{newsletter_type}"),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Test",
        sender="a@example.com",
        date_raw="",
        date_parsed=None,
        body_text="body text",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="body text",
        parse_warnings=(),
    )
    classified = ClassifiedMessage(
        parsed=parsed, newsletter_type=newsletter_type, classification_scores=()
    )
    return make_digest_entry(classified, no_ollama=False)


def test_builtin_summary_profile_set_loads() -> None:
    profile_set = get_builtin_summary_profile_set()
    assert profile_set.default_profile == "standard"
    assert profile_set.type_routes["unclassified"] == "standard"
    assert "rough" in profile_set.profiles


def test_profile_set_roundtrip() -> None:
    profile_set = get_builtin_summary_profile_set()
    roundtripped = summary_profile_set_from_dict(
        summary_profile_set_to_dict(profile_set)
    )
    assert roundtripped == profile_set


def test_type_routing_only_uses_canonical_labels_or_reserved_keys() -> None:
    profile_set = get_builtin_summary_profile_set()
    issues = validate_summary_profile_set(profile_set, get_canonical_newsletter_types())
    assert issues == []


def test_type_route_drift_visible() -> None:
    profile_set = summary_profile_set_from_dict(
        summary_profile_set_to_dict(get_builtin_summary_profile_set())
    )
    profile_set.type_routes["unknown"] = "standard"  # type: ignore[index]
    issues = validate_summary_profile_set(profile_set, get_canonical_newsletter_types())
    assert any(issue.code == "unknown_newsletter_type" for issue in issues)


def test_list_summary_profiles_ui_friendly() -> None:
    infos = list_summary_profiles(get_builtin_summary_profile_set())
    assert infos[0].name
    assert all(info.provider == "ollama" for info in infos)


def test_list_type_routes_ui_friendly() -> None:
    routes = list_type_routes(get_builtin_summary_profile_set())
    assert any(route.newsletter_type == "unclassified" for route in routes)


def test_type_routing_selects_expected_profile() -> None:
    profile_set = get_builtin_summary_profile_set()
    plan = resolve_summary_plan(
        [_entry("essay"), _entry("link_roundup")],
        profile_set,
        SummaryCliOptions(summary_type_routing=True),
    )
    assert plan.mode == "type_routed"
    assert plan.jobs_by_variant["default"][0].profile_name == "deep"
    assert plan.jobs_by_variant["default"][1].profile_name == "rough"


def test_summary_profile_overrides_type_routing() -> None:
    plan = resolve_summary_plan(
        [_entry("essay")],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_profile="rough", summary_type_routing=True),
    )
    assert plan.mode == "single_profile"
    assert plan.jobs_by_variant["default"][0].profile_name == "rough"


def test_summary_variants_override_type_routing() -> None:
    plan = resolve_summary_plan(
        [_entry("essay")],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(
            summary_variants=("rough", "deep"), summary_type_routing=True
        ),
    )
    assert plan.mode == "variants"
    assert plan.output_variants == ("rough", "deep")


def test_profile_validation_returns_structured_errors() -> None:
    profile_set = summary_profile_set_from_dict(
        summary_profile_set_to_dict(get_builtin_summary_profile_set())
    )
    profile_set.type_routes["essay"] = "missing"  # type: ignore[index]
    issues = validate_summary_profile_set(profile_set, get_canonical_newsletter_types())
    assert issues
    assert issues[0].path.startswith("type_routes.")


def test_require_valid_profile_set_raises() -> None:
    profile_set = summary_profile_set_from_dict(
        summary_profile_set_to_dict(get_builtin_summary_profile_set())
    )
    profile_set.type_routes["essay"] = "missing"  # type: ignore[index]
    try:
        require_valid_summary_profile_set(profile_set, get_canonical_newsletter_types())
    except Exception as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("Expected validation failure")

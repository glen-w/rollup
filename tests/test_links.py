"""Tests for deterministic link processing helpers."""

from __future__ import annotations

from rollup.links import (
    clean_anchor_text,
    clean_href,
    classify_link,
    classify_links,
    domain_for_display,
    is_raw_url_text,
    label_link,
    normalize_link_for_compare,
    prepare_links_for_render,
)
from rollup.models import LinkItem


def test_clean_anchor_text_preserves_meaningful_text() -> None:
    assert clean_anchor_text("  Register for event  ") == "Register for event"


def test_clean_anchor_text_rejects_raw_url_and_long_text() -> None:
    assert clean_anchor_text("https://example.com/path") is None
    assert clean_anchor_text("x" * 81) is None


def test_clean_anchor_text_rejects_generic_short_and_numeric_labels() -> None:
    assert clean_anchor_text("here") is None
    assert clean_anchor_text("click") is None
    assert clean_anchor_text("a") is None
    assert clean_anchor_text("7") is None


def test_is_raw_url_text() -> None:
    assert is_raw_url_text("https://example.com/path")
    assert not is_raw_url_text("Read more")


def test_domain_for_display_strips_www() -> None:
    assert domain_for_display("https://www.example.com/path") == "example.com"


def test_normalize_link_for_compare_strips_tracking_params() -> None:
    href = "https://example.com/post?utm_source=newsletter&token=abc&id=1#frag"
    assert normalize_link_for_compare(href) == "https://example.com/post?id=1"


def test_long_substack_tracking_url_gets_readable_label() -> None:
    href = (
        "https://substack.com/app-link/post?publication_id=123&post_id=456&token=abcdef"
        "&utm_source=email"
    )
    assert label_link(href) == "Open post"


def test_raw_url_anchor_text_is_replaced() -> None:
    href = "https://www.youtube.com/watch?v=abc123"
    assert label_link(href, text=href) == "Watch video"


def test_tracking_pixel_is_hidden_category() -> None:
    href = "https://eotrx.substackcdn.com/o/abc/p.gif?token=secret"
    assert classify_link(href) == "tracking_pixel"


def test_w3c_xhtml_is_junk() -> None:
    assert classify_link("http://www.w3.org/1999/xhtml") == "junk"


def test_sendgrid_wrapper_uses_anchor_text_before_falling_back() -> None:
    href = "https://u14608870.ct.sendgrid.net/ls/click?upn=abc123def456"
    assert classify_link(href, text="Report") == "document_pdf"
    assert (
        classify_link(href, text="Register", context="Register for the webinar")
        == "registration"
    )
    assert classify_link(href, text="Article") == "content"
    assert classify_link(href, text="click") == "unknown"


def test_substack_c_wrapper_uses_anchor_text_before_falling_back() -> None:
    href = "https://newsletter.substack.com/c/abc123"
    assert classify_link(href, text="Article") == "content"
    assert (
        classify_link(href, text="Register", context="Register for the event")
        == "registration"
    )
    assert classify_link(href, text="here") == "unknown"


def test_google_calendar_variants_collapse_for_display_but_keep_original_hrefs() -> (
    None
):
    links = [
        LinkItem(
            href="https://calendar.google.com/calendar/event?action=RESPOND&text=Demo&dates=20260702T100000Z/20260702T110000Z&rst=1",
            text="Respond",
            context=None,
            source_index=0,
        ),
        LinkItem(
            href="https://calendar.google.com/calendar/event?action=RESPOND&text=Demo&dates=20260702T100000Z/20260702T110000Z&rst=2",
            text="Respond",
            context=None,
            source_index=1,
        ),
    ]
    bundle = prepare_links_for_render(links, max_main=5, max_other=5)
    assert len(bundle.main_links) == 1
    assert bundle.main_links[0].href.endswith("rst=1")
    assert any(link.href.endswith("rst=2") for link in bundle.hidden_links)


def test_youtube_links_labelled_as_video() -> None:
    assert label_link("https://www.youtube.com/watch?v=abc123") == "Watch video"


def test_zoom_registration_labelled_correctly() -> None:
    href = "https://zoom.us/webinar/register/WN_abc123"
    assert label_link(href) == "Register for Zoom webinar"


def test_teams_event_labelled_correctly() -> None:
    href = "https://events.teams.microsoft.com/event/123/register"
    assert label_link(href) == "Register for Teams event"


def test_pdf_label_uses_heuristics_only() -> None:
    href = "https://example.org/download?file=annual-report.pdf"
    assert classify_link(href, text="Annual report") == "document_pdf"
    assert (
        label_link(href, text="https://example.org/download?file=annual-report.pdf")
        == "Open PDF"
    )


def test_author_profile_classified_correctly() -> None:
    href = "https://example.com/authors/jane-doe"
    assert classify_link(href) == "author_profile"
    assert label_link(href) == "Author profile"


def test_exact_duplicate_hrefs_collapse_for_display() -> None:
    link = LinkItem("https://example.com/story", "Read more", None, 0)
    bundle = prepare_links_for_render([link, LinkItem(link.href, "Read more", None, 1)])
    assert len(bundle.main_links) == 1
    assert any(
        hidden.hidden_reason == "duplicate_for_display"
        for hidden in bundle.hidden_links
    )


def test_prepare_links_separates_other_and_hidden_links() -> None:
    links = [
        LinkItem("https://example.com/post/1", "Read article", None, 0),
        LinkItem(
            "https://calendar.google.com/calendar/event?action=VIEW", None, None, 1
        ),
        LinkItem("https://example.com/preferences", "Manage preferences", None, 2),
    ]
    bundle = prepare_links_for_render(links, max_main=1, max_other=5)
    assert len(bundle.main_links) == 1
    assert len(bundle.other_links) == 1
    assert bundle.hidden_links[0].category == "unsubscribe_preferences"


def test_mailchimp_preference_link_is_hidden() -> None:
    href = "https://highseasalliance.us9.list-manage.com/profile?u=abc&id=def"
    assert (
        classify_link(href, text="update your preferences") == "unsubscribe_preferences"
    )
    bundle = prepare_links_for_render(
        [
            LinkItem("https://example.com/article", "Read article", None, 0),
            LinkItem(href, "update your preferences", None, 1),
        ],
        max_main=5,
        max_other=5,
    )
    assert len(bundle.main_links) == 1
    assert bundle.main_links[0].label == "Read article"
    assert all(
        link.category == "unsubscribe_preferences" for link in bundle.hidden_links
    )


def test_unknown_links_are_not_promoted_to_key_links() -> None:
    bundle = prepare_links_for_render(
        [
            LinkItem("https://example.com/story", "Read article", None, 0),
            LinkItem(
                "https://u14608870.ct.sendgrid.net/ls/click?upn=abc", "click", None, 1
            ),
        ],
        max_main=5,
        max_other=5,
    )
    assert len(bundle.main_links) == 1
    assert bundle.main_links[0].category == "content"
    assert len(bundle.other_links) == 1
    assert bundle.other_links[0].category == "unknown"


def test_classify_links_preserves_original_href() -> None:
    href = "https://example.com/post?token=secret&utm_source=email"
    classified = classify_links([LinkItem(href, "Open", None, 0)])
    assert classified[0].href == href


def test_clean_href_strips_trailing_paren_and_bracket() -> None:
    assert clean_href("https://x.com/a).") == "https://x.com/a"
    assert clean_href("https://x.com/a]") == "https://x.com/a"


def test_clean_href_preserves_valid_path_and_extension() -> None:
    assert clean_href("https://x.com/report.pdf") == "https://x.com/report.pdf"
    assert clean_href("https://en.wikipedia.org/wiki/Foo_(bar)") == (
        "https://en.wikipedia.org/wiki/Foo_(bar)"
    )


def test_clean_href_strips_trailing_period_from_prose_capture() -> None:
    assert clean_href("https://example.com/foo.") == "https://example.com/foo"


def test_clean_href_preserves_percent_encoded_paths() -> None:
    href = "https://x.com/path%2Fto%29file"
    assert clean_href(href) == href


def test_clean_href_preserves_percent_encoded_query_values() -> None:
    href = "https://x.com/search?q=hello%20world"
    assert clean_href(href) == href


def test_classify_links_applies_clean_href() -> None:
    classified = classify_links(
        [LinkItem("https://example.com/article).", "Open", None, 0)]
    )
    assert classified[0].href == "https://example.com/article"


def test_dedup_prefers_shorter_normalised_query() -> None:
    bundle = prepare_links_for_render(
        [
            LinkItem(
                "https://example.com/story?utm_source=email", "Tracked", None, 0
            ),
            LinkItem("https://example.com/story", "Clean", None, 1),
        ],
        max_main=5,
        max_other=5,
    )
    assert len(bundle.main_links) == 1
    assert bundle.main_links[0].href == "https://example.com/story"
    assert any(
        link.hidden_reason == "duplicate_for_display" for link in bundle.hidden_links
    )


def test_dedup_prefers_shorter_url_when_dedupe_key_matches() -> None:
    bundle = prepare_links_for_render(
        [
            LinkItem(
                "https://substack.com/app-link/post?publication_id=1&post_id=2&token=abc",
                None,
                None,
                0,
            ),
            LinkItem(
                "https://substack.com/app-link/post?publication_id=1&post_id=2",
                None,
                None,
                1,
            ),
        ],
        max_main=5,
        max_other=5,
    )
    assert len(bundle.main_links) == 1
    assert bundle.main_links[0].href == (
        "https://substack.com/app-link/post?publication_id=1&post_id=2"
    )
    assert any(
        link.hidden_reason == "duplicate_for_display" for link in bundle.hidden_links
    )

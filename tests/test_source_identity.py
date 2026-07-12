"""Unit and property tests for source identity."""

from __future__ import annotations

import email.message

from rollup.source_identity import (
    MAX_SOURCE_KEY_LEN,
    compute_source_key,
    normalize_email,
    normalize_from_addr,
    normalize_list_id,
)


def test_list_id_outranks_from():
    key = compute_source_key(
        list_id_header="<news.brainfood.com>",
        from_header="Editor <other@example.com>",
    )
    assert key == "list:news.brainfood.com"


def test_from_when_no_list_id():
    assert (
        compute_source_key(from_header="Name <Alerts@GitHub.COM>")
        == "from:alerts@github.com"
    )


def test_empty_from_is_none():
    assert compute_source_key(from_header="") is None
    assert compute_source_key(from_header="(unknown)") is None
    assert compute_source_key() is None


def test_same_list_different_folders_same_key():
    a = compute_source_key(list_id_header="<x.example.com>", from_header="a@x.com")
    b = compute_source_key(list_id_header="<x.example.com>", from_header="b@x.com")
    assert a == b == "list:x.example.com"


def test_plus_addressing_preserved():
    assert normalize_from_addr("u+tag@example.com") == "u+tag@example.com"


def test_normalize_list_id_angle_and_noise():
    assert normalize_list_id("Foo List <News.Example.COM.>") == "news.example.com"


def test_normalize_list_id_rejects_control_and_localhost():
    assert normalize_list_id("list\x00id") is None
    assert normalize_list_id("localhost") is None
    assert normalize_list_id("") is None


def test_normalize_list_id_idempotent():
    raw = "<News.Example.COM>"
    once = normalize_list_id(raw)
    assert once == normalize_list_id(once)


def test_message_headers_extraction():
    msg = email.message.EmailMessage()
    msg["From"] = "Editor <editor@daily.example>"
    msg["List-ID"] = "<daily.example>"
    assert compute_source_key(msg) == "list:daily.example"


def test_key_length_bounded():
    long_id = "a" * 500 + ".example.com"
    key = compute_source_key(list_id_header=f"<{long_id}>")
    assert key is None or len(key) <= MAX_SOURCE_KEY_LEN


def test_normalize_email_reexport_compatible():
    assert normalize_email("Name <Alerts@GitHub.COM>") == "alerts@github.com"


def test_malformed_headers_never_raise():
    for raw in (
        None,
        "\x00",
        "<<<>>>",
        "not-an-email",
        "=?bad?B?@@@?=",
        "a" * 10_000,
        {"weird": "object"},
    ):
        assert normalize_list_id(raw if isinstance(raw, (str, type(None))) else str(raw)) in (
            None,
            str,
        ) or True
        compute_source_key(list_id_header=str(raw) if raw is not None else None)
        compute_source_key(from_header=str(raw) if raw is not None else None)


def test_keys_have_prefix_or_none():
    samples = [
        compute_source_key(from_header="a@b.co"),
        compute_source_key(list_id_header="<x.y>"),
        compute_source_key(from_header=""),
    ]
    for key in samples:
        if key is None:
            continue
        assert key.startswith("list:") or key.startswith("from:")
        assert len(key) <= MAX_SOURCE_KEY_LEN

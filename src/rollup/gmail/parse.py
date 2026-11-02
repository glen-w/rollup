"""Map cached raw Gmail messages onto ``ParsedMessage`` via the mbox parser.

Gmail delivers RFC 822 bytes, so the existing ``parse_message`` is reused
unchanged: a newsletter that arrives via Thunderbird mbox and via Gmail yields
the same ``message_key`` (``mid:`` + Message-ID) and ``content_hash``.
"""

from __future__ import annotations

import email
from email import policy

from rollup.gmail.session import GmailRawMessage
from rollup.models import ParsedMessage
from rollup.parse import parse_message

GMAIL_RELATIVE_PREFIX = "gmail"


def raw_to_parsed_message(
    raw: GmailRawMessage,
    *,
    folder_name: str,
    label_name: str,
    max_body_chars: int,
    max_display_links: int,
) -> ParsedMessage:
    msg = email.message_from_bytes(raw.raw, policy=policy.compat32)
    return parse_message(
        msg,
        folder_name,
        f"{GMAIL_RELATIVE_PREFIX}/{label_name}",
        max_body_chars,
        max_display_links,
    )

"""Generate synthetic mbox test fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "Newsletters.sbd"


def _mbox_line(date: datetime | None = None) -> str:
    dt = date or datetime.now()
    return f"From - {dt.strftime('%a %b %d %H:%M:%S %Y')}\n"


def _write_mbox(path: Path, messages: list[EmailMessage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    for msg in messages:
        parts.append(_mbox_line())
        parts.append(msg.as_string())
        if not parts[-1].endswith("\n"):
            parts.append("\n")
    path.write_text("".join(parts), encoding="utf-8")


def _plain_msg(
    subject: str,
    sender: str,
    body: str,
    message_id: str | None = None,
    date: datetime | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "reader@example.com"
    if date:
        msg["Date"] = date.strftime("%a, %d %b %Y %H:%M:%S %z")
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content(body)
    return msg


def _html_msg(
    subject: str,
    sender: str,
    html: str,
    message_id: str | None = None,
    date: datetime | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "reader@example.com"
    if date:
        msg["Date"] = date.strftime("%a, %d %b %Y %H:%M:%S %z")
    if message_id:
        msg["Message-ID"] = message_id
    msg.add_alternative(html, subtype="html")
    return msg


def main() -> None:
    now = datetime.now().astimezone()
    recent = now - timedelta(days=2)
    old = now - timedelta(days=30)

    # Flat five folders
    _write_mbox(
        FIXTURE_ROOT / "brainfood",
        [
            _plain_msg(
                "Brainfood Weekly",
                "brain@news.example",
                "Quick thoughts on learning and curiosity. " * 20,
                "<brain1@example.com>",
                recent,
            ),
        ],
    )
    _write_mbox(
        FIXTURE_ROOT / "enviro",
        [
            _plain_msg(
                "Climate Update",
                "enviro@ngo.example",
                "## Section One\n\nPolicy news.\n\n## Section Two\n\nReport out.\n\n- item\n- item\n"
                * 15,
                "<enviro1@example.com>",
                recent,
            ),
        ],
    )
    _write_mbox(
        FIXTURE_ROOT / "hoops",
        [
            _plain_msg(
                "Game Recap",
                "hoops@nba.example",
                "Short recap of last night.",
                "<hoops1@example.com>",
                recent,
            ),
            _plain_msg(
                "Old Game",
                "hoops@nba.example",
                "Old game outside window.",
                "<hoops2@example.com>",
                old,
            ),
        ],
    )
    _write_mbox(
        FIXTURE_ROOT / "misc",
        [
            _plain_msg(
                "Misc Newsletter",
                "misc@example.com",
                "Misc content here.",
                None,  # fallback key test
                recent,
            ),
        ],
    )
    _write_mbox(
        FIXTURE_ROOT / "tech",
        [
            _html_msg(
                "Tech Roundup",
                "tech@example.com",
                "<html><body><h1>Links</h1>"
                + "".join(
                    f'<a href="https://example.com/{i}">Link {i}</a> '
                    for i in range(12)
                )
                + "<p>Some prose about tech.</p></body></html>",
                "<tech1@example.com>",
                recent,
            ),
        ],
    )
    _write_mbox(
        FIXTURE_ROOT / "trackerwall",
        [
            _html_msg(
                "Tracker Wall Digest",
                "links@example.com",
                (
                    "<html><body><h1>Useful links</h1>"
                    '<p><a href="https://substack.com/app-link/post?publication_id=111&post_id=222&token=abcdef&utm_source=email">'
                    "https://substack.com/app-link/post?publication_id=111&post_id=222&token=abcdef&utm_source=email"
                    "</a></p>"
                    '<p><a href="https://events.teams.microsoft.com/event/123/register">Register</a></p>'
                    '<p><a href="https://calendar.google.com/calendar/event?action=RESPOND&text=Demo&dates=20260702T100000Z/20260702T110000Z&rst=1">Add to calendar</a></p>'
                    '<p><a href="https://calendar.google.com/calendar/event?action=RESPOND&text=Demo&dates=20260702T100000Z/20260702T110000Z&rst=2">RSVP copy</a></p>'
                    '<p><a href="https://u14608870.ct.sendgrid.net/ls/click?upn=abc123def456">https://u14608870.ct.sendgrid.net/ls/click?upn=abc123def456</a></p>'
                    '<p><a href="https://eotrx.substackcdn.com/o/abc/p.gif?token=secret">pixel</a></p>'
                    '<p><a href="http://www.w3.org/1999/xhtml">xhtml</a></p>'
                    "</body></html>"
                ),
                "<trackerwall@example.com>",
                recent,
            ),
        ],
    )

    # Empty .msf sidecars (ignored)
    for name in ("brainfood", "enviro", "hoops", "misc", "tech", "trackerwall"):
        (FIXTURE_ROOT / f"{name}.msf").write_text("", encoding="utf-8")

    # Nested classify fixture
    classify_dir = FIXTURE_ROOT / "classify.sbd"
    _write_mbox(
        classify_dir / "short_update",
        [
            _plain_msg(
                "Short Update",
                "short@example.com",
                "Brief update. Meeting tomorrow.",
                "<short@example.com>",
                recent,
            ),
        ],
    )
    essay_body = " ".join(["This is a long essay paragraph about ideas."] * 200)
    _write_mbox(
        classify_dir / "essay",
        [
            _plain_msg(
                "Long Essay",
                "essay@example.com",
                essay_body,
                "<essay@example.com>",
                recent,
            )
        ],
    )
    links_html = (
        "<html><body>"
        + "".join(
            f'<a href="https://news.example/{i}">Story {i}</a> word ' for i in range(15)
        )
        + "text " * 50
        + "</body></html>"
    )
    _write_mbox(
        classify_dir / "link_roundup",
        [
            _html_msg(
                "Link Roundup",
                "links@example.com",
                links_html,
                "<links@example.com>",
                recent,
            ),
        ],
    )
    multi_body = "\n".join(
        [f"## Section {i}\n\nBullet content.\n- point\n- point\n" for i in range(5)]
    )
    _write_mbox(
        classify_dir / "multi_section_digest",
        [
            _plain_msg(
                "Multi Section",
                "multi@example.com",
                multi_body,
                "<multi@example.com>",
                recent,
            ),
        ],
    )
    _write_mbox(classify_dir / "unclassified_empty", [])

    # Undated message in misc
    undated = _plain_msg(
        "No Date Newsletter", "nodate@example.com", "This has no date header."
    )
    undated.__delitem__("Date")
    _append_to_mbox(FIXTURE_ROOT / "misc", [undated])

    print(f"Generated fixtures under {FIXTURE_ROOT}")


def _append_to_mbox(path: Path, messages: list[EmailMessage]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    parts = [existing]
    for msg in messages:
        parts.append(_mbox_line())
        parts.append(msg.as_string())
        if not parts[-1].endswith("\n"):
            parts.append("\n")
    path.write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()

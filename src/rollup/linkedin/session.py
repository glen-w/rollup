"""LinkedIn session cookie loading from environment (never TOML)."""

from __future__ import annotations

import os

import requests

LI_AT_ENV = "ROLLUP_LINKEDIN_LI_AT"
JSESSIONID_ENV = "ROLLUP_LINKEDIN_JSESSIONID"


class LinkedInSessionError(RuntimeError):
    """LinkedIn session is missing or invalid."""


def linkedin_cookie_configured() -> bool:
    """True when the primary li_at cookie env var is set."""
    return bool(os.environ.get(LI_AT_ENV, "").strip())


def jsession_id_configured() -> bool:
    """True when JSESSIONID (Voyager CSRF) is set."""
    return bool(os.environ.get(JSESSIONID_ENV, "").strip())


def normalize_jsession_id(raw: str) -> str:
    """Strip surrounding quotes and common DevTools copy artifacts from ajax:… values."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    # DevTools sometimes copies JSESSIONID as :"ajax:…" or :ajax:…
    if text.startswith(':"'):
        text = text[2:]
    elif text.startswith(":"):
        text = text[1:]
    return text.strip().strip('"').strip("'")


def voyager_headers(session: requests.Session) -> dict[str, str]:
    """CSRF + Rest.li headers for /voyager/api calls."""
    csrf = ""
    for cookie in session.cookies:
        if cookie.name == "JSESSIONID":
            csrf = normalize_jsession_id(cookie.value or "")
            break
    if not csrf:
        csrf = normalize_jsession_id(os.environ.get(JSESSIONID_ENV, ""))
    headers = {
        "Accept": "application/vnd.linkedin.normalized+json+2.1",
        "x-restli-protocol-version": "2.0.0",
        "x-li-lang": "en_US",
        "Referer": "https://www.linkedin.com/feed/",
    }
    if csrf:
        headers["csrf-token"] = csrf
        headers["Csrf-Token"] = csrf
    return headers


def build_linkedin_session() -> requests.Session:
    """Build a requests session with LinkedIn cookies from the environment."""
    li_at = os.environ.get(LI_AT_ENV, "").strip()
    if not li_at:
        raise LinkedInSessionError(
            f"LinkedIn fetch requires {LI_AT_ENV} in the environment "
            "(session cookie; never stored in TOML)"
        )
    session = requests.Session()
    session.cookies.set("li_at", li_at, domain=".linkedin.com", path="/")
    jsession = normalize_jsession_id(os.environ.get(JSESSIONID_ENV, "").strip())
    if jsession:
        # LinkedIn expects quoted JSESSIONID cookie values.
        session.cookies.set(
            "JSESSIONID", f'"{jsession}"', domain=".linkedin.com", path="/"
        )
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session

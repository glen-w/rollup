"""Unwrap Scholar redirect URLs and drop junk / PDF destinations."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from rollup.links import clean_href

SCHOLAR_URL_PATH = "/scholar_url"
ARXIV_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
SCHOLAR_HOST_SUFFIXES = (
    "scholar.google.com",
    "scholar.googleusercontent.com",
)

JUNK_ANCHOR_TEXTS = frozenset(
    {
        "related articles",
        "cite",
        "save",
        "library",
        "html",
        "pdf",
        "cached",
        "unsubscribe",
        "alert preferences",
        "manage alerts",
        "all versions",
        "cited by",
        "view all",
        "sign in",
        "my citations",
        "my profile",
        "settings",
        "help",
        "privacy",
        "terms",
    }
)

_JUNK_HOST_SUFFIXES = (
    "accounts.google.com",
    "support.google.com",
    "policies.google.com",
)
_JUNK_PATH_MARKERS = (
    "/citations",
    "/scholar_alerts",
    "/scholar?q=related",
    "/scholar_lookup",
)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _is_scholar_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in SCHOLAR_HOST_SUFFIXES)


def unwrap_scholar_url(url: str) -> str:
    """Follow scholar.google.com/scholar_url?url=… to the inner destination."""
    cleaned = clean_href(url) or (url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    if not _is_scholar_host(host):
        return cleaned
    if not (parsed.path or "").startswith(SCHOLAR_URL_PATH):
        return cleaned
    inner = parse_qs(parsed.query).get("url", [None])[0]
    if not inner:
        return cleaned
    return unquote(inner).strip()


def rewrite_arxiv_pdf(url: str) -> str:
    """Map arXiv PDF URLs to the abstract page (HTML we can extract)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ARXIV_HOSTS:
        return url
    path = parsed.path or ""
    if not path.startswith("/pdf/"):
        return url
    ident = path[len("/pdf/") :].removesuffix(".pdf").strip("/")
    if not ident:
        return url
    return urlunparse(("https", "arxiv.org", f"/abs/{ident}", "", "", ""))


def is_pdf_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return path.endswith(".pdf")


def is_junk_paper_url(url: str) -> bool:
    host = _host(url)
    if not host:
        return True
    if any(host == suffix or host.endswith("." + suffix) for suffix in _JUNK_HOST_SUFFIXES):
        return True
    lowered = url.lower()
    if any(marker in lowered for marker in _JUNK_PATH_MARKERS):
        return True
    if _is_scholar_host(host):
        return True
    return False


def is_junk_anchor_text(text: str | None) -> bool:
    cleaned = " ".join((text or "").split()).strip().lower()
    if not cleaned:
        return False
    if cleaned in JUNK_ANCHOR_TEXTS:
        return True
    if cleaned.startswith("cited by"):
        return True
    return False


def normalize_paper_url(url: str) -> tuple[str | None, bool]:
    """Return (canonical destination, skip_fetch_because_pdf).

    ``None`` destination means the URL is not a paper landing page.
    """
    unwrapped = unwrap_scholar_url(url)
    if not unwrapped:
        return None, False
    rewritten = rewrite_arxiv_pdf(unwrapped)
    if is_junk_paper_url(rewritten):
        return None, False
    if is_pdf_url(rewritten):
        return rewritten, True
    return rewritten, False

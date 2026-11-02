"""OPDS 1.2 Atom acquisition feed for one digest run."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from rollup.addons.artifact_write import atomic_write_digest_artifact
from rollup.models import DigestReport
from rollup.render import digest_output_stem

ATOM_NS = "http://www.w3.org/2005/Atom"
OPDS_REL_ACQUISITION = "http://opds-spec.org/acquisition"
OPDS_REL_ACQUISITION_OPEN = "http://opds-spec.org/acquisition/open-access"

# (filename suffix, MIME type, OPDS/Atom rel)
_ACQUISITION_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    (".epub", "application/epub+zip", OPDS_REL_ACQUISITION),
    (".html", "text/html", "alternate"),
    (".md", "text/markdown", "alternate"),
    (".json", "application/json", "alternate"),
    (".txt", "text/plain", "alternate"),
    (".xteink.md", "text/markdown", "alternate"),
)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _xml_text(value: str) -> str:
    return escape(value, {"'": "&apos;", '"': "&quot;"})


def briefing_title(report: DigestReport) -> str:
    start = report.window_start.date().isoformat()
    end = report.window_end.date().isoformat()
    if start == end:
        return f"Rollup — {end}"
    return f"Rollup — {start} to {end}"


def briefing_summary(report: DigestReport) -> str:
    synthesis = getattr(report, "briefing_synthesis", None)
    if synthesis is not None and synthesis.headline:
        return synthesis.headline
    dated = report.stats.dated_included
    undated = report.stats.undated_needing_review
    return (
        f"{dated} dated item(s), {undated} undated, "
        f"{report.stats.folders_scanned} folder(s)"
    )


def sibling_artifact_names(
    output_dir: Path,
    generated_at: datetime,
    *,
    run_id_short: str | None = None,
) -> list[tuple[str, str, str]]:
    """Return (filename, mime, rel) for artifacts already beside this stem."""
    stem = digest_output_stem(generated_at, run_id_short=run_id_short)
    found: list[tuple[str, str, str]] = []
    for suffix, mime, rel in _ACQUISITION_CANDIDATES:
        name = f"{stem}{suffix}"
        if (output_dir / name).is_file():
            found.append((name, mime, rel))
    return found


def render_opds_atom(
    report: DigestReport,
    output_dir: Path,
    *,
    run_id_short: str | None = None,
    feed_id: str | None = None,
    self_href: str | None = None,
    acquisition_hrefs: dict[str, str] | None = None,
) -> str:
    """Build a one-entry OPDS acquisition feed for this briefing.

    ``acquisition_hrefs`` maps artifact filenames to link hrefs (absolute URLs
    for the loopback catalog). Default hrefs are the filenames themselves so a
    file-based OPDS client can open siblings next to the Atom document.
    """
    title = briefing_title(report)
    summary = briefing_summary(report)
    updated = _utc_iso(report.generated_at)
    rid = run_id_short or report.generated_at.strftime("%Y%m%dT%H%M%SZ")
    fid = feed_id or f"urn:rollup:opds:{rid}"
    entry_id = f"urn:rollup:digest:{rid}"
    artifacts = sibling_artifact_names(
        output_dir, report.generated_at, run_id_short=run_id_short
    )
    hrefs = acquisition_hrefs or {}

    links: list[str] = []
    if self_href:
        links.append(
            f'  <link rel="self" href={quoteattr(self_href)} '
            f'type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>'
        )
    for filename, mime, rel in artifacts:
        href = hrefs.get(filename, filename)
        links.append(
            f"  <link rel={quoteattr(rel)} href={quoteattr(href)} "
            f"type={quoteattr(mime)}/>"
        )
    if not artifacts:
        # Feed is still valid; clients see a titled entry without files yet.
        links.append(
            f'  <link rel="alternate" href={quoteattr(hrefs.get("__missing__", "#"))} '
            f'type="text/html"/>'
        )

    link_xml = "\n".join(links)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<feed xmlns="{ATOM_NS}" '
        'xmlns:dc="http://purl.org/dc/terms/" '
        'xmlns:opds="http://opds-spec.org/2010/catalog">\n'
        f"  <id>{_xml_text(fid)}</id>\n"
        f"  <title>{_xml_text(title)}</title>\n"
        f"  <updated>{updated}</updated>\n"
        "  <author><name>Rollup</name></author>\n"
        "  <entry>\n"
        f"    <title>{_xml_text(title)}</title>\n"
        f"    <id>{_xml_text(entry_id)}</id>\n"
        f"    <updated>{updated}</updated>\n"
        f'    <content type="text">{_xml_text(summary)}</content>\n'
        f"{link_xml.replace('  <link', '    <link')}\n"
        "  </entry>\n"
        "</feed>\n"
    )


def render_opds_catalog(
    entries: list[dict[str, str]],
    *,
    feed_id: str,
    title: str,
    updated: datetime,
    self_href: str,
) -> str:
    """Multi-entry OPDS acquisition catalog (loopback web)."""
    updated_iso = _utc_iso(updated)
    body: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<feed xmlns="{ATOM_NS}" '
        'xmlns:dc="http://purl.org/dc/terms/" '
        'xmlns:opds="http://opds-spec.org/2010/catalog">',
        f"  <id>{_xml_text(feed_id)}</id>",
        f"  <title>{_xml_text(title)}</title>",
        f"  <updated>{updated_iso}</updated>",
        "  <author><name>Rollup</name></author>",
        f'  <link rel="self" href={quoteattr(self_href)} '
        'type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>',
    ]
    for entry in entries:
        body.append("  <entry>")
        body.append(f"    <title>{_xml_text(entry['title'])}</title>")
        body.append(f"    <id>{_xml_text(entry['id'])}</id>")
        body.append(f"    <updated>{_xml_text(entry['updated'])}</updated>")
        if entry.get("summary"):
            body.append(
                f'    <content type="text">{_xml_text(entry["summary"])}</content>'
            )
        for link in entry.get("links", ()):
            body.append(f"    {link}")
        body.append("  </entry>")
    body.append("</feed>")
    return "\n".join(body) + "\n"


def opds_link_xml(*, rel: str, href: str, mime: str) -> str:
    return (
        f"<link rel={quoteattr(rel)} href={quoteattr(href)} "
        f"type={quoteattr(mime)}/>"
    )


def atomic_write_opds_digest(
    output_dir: Path,
    generated_at: datetime,
    xml: str,
    *,
    run_id_short: str | None = None,
) -> Path:
    return atomic_write_digest_artifact(
        output_dir,
        generated_at,
        xml,
        extension="atom.xml",
        run_id_short=run_id_short,
    )

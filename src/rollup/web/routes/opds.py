"""Loopback OPDS catalog of finished briefings (not a read-later library)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, current_app, g, request

from rollup.addons.opds.render import opds_link_xml, render_opds_catalog
from rollup.output_archive import resolve_output_artifact
from rollup.utc import now_utc

bp = Blueprint("opds", __name__)

_OPDS_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition;charset=utf-8"
_CATALOG_LIMIT = 50

_KIND_MIME = {
    "html": "text/html",
    "md": "text/markdown",
    "epub": "application/epub+zip",
    "atom": "application/atom+xml",
}


def _base_url() -> str:
    return request.host_url.rstrip("/")


def _artifact_url(run_id: str, kind: str) -> str:
    return f"{_base_url()}/artifacts/{run_id}/{kind}"


def _has_artifact(output_dir: Path, rel: str | None, suffix: str) -> bool:
    if not rel:
        return False
    rel_path = Path(rel)
    name = rel_path.name
    if name.endswith(".md") and not name.endswith(".xteink.md"):
        stem = name[: -len(".md")]
    else:
        stem = rel_path.stem
    sibling = str(rel_path.with_name(f"{stem}{suffix}"))
    return resolve_output_artifact(output_dir, sibling) is not None


@bp.get("/opds")
def opds_catalog():
    output_dir = Path(current_app.config["OUTPUT_DIR"])
    rows = g.db.execute(
        """SELECT run_id, started_at, completed_at, status, window_start,
                  window_end, messages_included, markdown_relpath, html_relpath
           FROM rollup_runs
           WHERE status IN ('success', 'partial')
             AND IFNULL(messages_included, 0) > 0
           ORDER BY started_at DESC, run_id DESC
           LIMIT ?""",
        (_CATALOG_LIMIT,),
    ).fetchall()

    entries: list[dict] = []
    for row in rows:
        (
            run_id,
            started_at,
            completed_at,
            _status,
            window_start,
            window_end,
            messages_included,
            md_rel,
            html_rel,
        ) = row
        title_end = (window_end or started_at or "")[:10]
        title_start = (window_start or "")[:10]
        if title_start and title_end and title_start != title_end:
            title = f"Rollup — {title_start} to {title_end}"
        else:
            title = f"Rollup — {title_end or run_id[:8]}"
        updated = completed_at or started_at or now_utc().isoformat()
        links: list[str] = []
        if html_rel:
            links.append(
                opds_link_xml(
                    rel="alternate",
                    href=_artifact_url(run_id, "html"),
                    mime=_KIND_MIME["html"],
                )
            )
        if md_rel:
            links.append(
                opds_link_xml(
                    rel="alternate",
                    href=_artifact_url(run_id, "md"),
                    mime=_KIND_MIME["md"],
                )
            )
            if _has_artifact(output_dir, md_rel, ".epub"):
                links.append(
                    opds_link_xml(
                        rel="http://opds-spec.org/acquisition",
                        href=_artifact_url(run_id, "epub"),
                        mime=_KIND_MIME["epub"],
                    )
                )
            if _has_artifact(output_dir, md_rel, ".atom.xml"):
                links.append(
                    opds_link_xml(
                        rel="alternate",
                        href=_artifact_url(run_id, "atom"),
                        mime=_KIND_MIME["atom"],
                    )
                )
        if not links:
            continue
        entries.append(
            {
                "title": title,
                "id": f"urn:rollup:run:{run_id}",
                "updated": str(updated),
                "summary": f"{int(messages_included or 0)} item(s)",
                "links": links,
            }
        )

    updated_dt = datetime.now(timezone.utc)
    xml = render_opds_catalog(
        entries,
        feed_id="urn:rollup:opds:catalog",
        title="Rollup briefings",
        updated=updated_dt,
        self_href=f"{_base_url()}/opds",
    )
    return Response(xml, mimetype=_OPDS_TYPE)

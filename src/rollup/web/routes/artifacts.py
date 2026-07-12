"""Safe artifact serving from indexed relative paths."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, g, send_file

from rollup.safety import is_inside
from rollup.web_ids import IdError, validate_run_id

bp = Blueprint("artifacts", __name__)


@bp.get("/artifacts/<run_id>/<kind>")
def serve_artifact(run_id: str, kind: str):
    try:
        run_id = validate_run_id(run_id)
    except IdError:
        return "Invalid run id", 404
    if kind not in {"md", "html", "manifest"}:
        return "Unknown artifact kind", 404

    row = g.db.execute(
        """SELECT markdown_relpath, html_relpath, manifest_relpath
           FROM rollup_runs WHERE run_id = ?""",
        (run_id,),
    ).fetchone()
    if row is None:
        return "Run not found", 404

    md_rel, html_rel, manifest_rel = row
    if kind == "md":
        rel, root = md_rel, Path(current_app.config["OUTPUT_DIR"])
        mimetype = "text/markdown; charset=utf-8"
        as_attachment = True
        download_name = Path(rel).name if rel else "digest.md"
    elif kind == "html":
        rel, root = html_rel, Path(current_app.config["OUTPUT_DIR"])
        mimetype = "text/html; charset=utf-8"
        as_attachment = True
        download_name = Path(rel).name if rel else "digest.html"
    else:
        rel, root = manifest_rel, Path(current_app.config["STATE_DIR"])
        mimetype = "application/json"
        as_attachment = True
        download_name = Path(rel).name if rel else "manifest.json"

    if not rel:
        return "Artifact not indexed", 404
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        return "Unsafe artifact path", 400

    candidate = (root / rel).resolve()
    if not is_inside(candidate, root.resolve()):
        return "Artifact path escapes root", 400
    if not candidate.is_file():
        return "Artifact missing on disk", 404

    return send_file(
        candidate,
        mimetype=mimetype,
        as_attachment=as_attachment,
        download_name=download_name,
    )

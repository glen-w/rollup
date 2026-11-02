"""Digest file naming and atomic staged writes (fsync + rename)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from rollup.assets import FAVICON_FILENAME, LOGO_FILENAME

logger = logging.getLogger(__name__)


def write_branding_assets(output_dir: Path) -> None:
    """Copy logo and favicon beside rollup output files.

    Digests write the compact grayscale e-ink logo as ``rollup_logo.png`` so
    Markdown/HTML on devices like Xteink X3 load quickly; the web UI still
    serves the color asset from the package.
    """
    from rollup.assets import asset_bytes, digest_logo_bytes

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / LOGO_FILENAME).write_bytes(digest_logo_bytes())
    (output_dir / FAVICON_FILENAME).write_bytes(asset_bytes(FAVICON_FILENAME))


def digest_output_stem(
    generated_at: datetime,
    variant_name: str | None = None,
    run_id_short: str | None = None,
) -> str:
    """Filename stem for digest outputs.

    Includes second precision and optional short run_id so same-second
    concurrent runs do not collide.
    """
    timestamp = generated_at.strftime("%Y-%m-%dT%H%M%SZ")
    if generated_at.tzinfo is not None:
        # Prefer UTC-style compact stamp; fall back to local wall time format.
        timestamp = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    rid = f"-{run_id_short}" if run_id_short else ""
    suffix = f".{variant_name}" if variant_name else ""
    return f"{timestamp}{rid}-newsletter-digest{suffix}"


def cleanup_stale_temps(output_dir: Path) -> None:
    for path in output_dir.glob(".tmp-*"):
        try:
            path.unlink()
            logger.debug("Removed stale temp file %s", path)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)


def _write_synced(path: Path, content: str) -> None:
    """Write text and fsync before the caller renames it into place."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write_digest(
    output_dir: Path,
    generated_at: datetime,
    markdown: str,
    html_content: str,
    variant_name: str | None = None,
    run_id_short: str | None = None,
) -> tuple[Path, Path]:
    """Write MD and HTML atomically via fsynced temp files + rename."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_stale_temps(output_dir)
    write_branding_assets(output_dir)
    stem = digest_output_stem(generated_at, variant_name, run_id_short=run_id_short)
    final_md = output_dir / f"{stem}.md"
    final_html = output_dir / f"{stem}.html"
    if final_md.exists() or final_html.exists():
        raise FileExistsError(f"Digest output stem already exists: {stem} — refusing to overwrite")
    tmp_md = output_dir / f".tmp-{stem}.md"
    tmp_html = output_dir / f".tmp-{stem}.html"

    paths_to_clean = (tmp_md, tmp_html, final_md, final_html)

    def _cleanup() -> None:
        for path in paths_to_clean:
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                logger.warning("Could not remove %s: %s", path, exc)

    try:
        _write_synced(tmp_md, markdown)
        _write_synced(tmp_html, html_content)
        tmp_md.rename(final_md)
        tmp_html.rename(final_html)
    except Exception:
        _cleanup()
        raise
    return final_md, final_html

"""Atomic single-file digest artifact writes (json/txt/epub)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rollup.fsutil import atomic_write_bytes, atomic_write_text
from rollup.render import digest_output_stem


def digest_artifact_path(
    output_dir: Path,
    generated_at: datetime,
    extension: str,
    *,
    run_id_short: str | None = None,
) -> Path:
    """Path for a single-file writer beside the core digest stem."""
    ext = extension.lstrip(".")
    stem = digest_output_stem(generated_at, run_id_short=run_id_short)
    return output_dir / f"{stem}.{ext}"


def atomic_write_digest_artifact(
    output_dir: Path,
    generated_at: datetime,
    content: str | bytes,
    *,
    extension: str,
    run_id_short: str | None = None,
) -> Path:
    """Write a single digest artifact; refuse to overwrite an existing file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = digest_artifact_path(
        output_dir, generated_at, extension, run_id_short=run_id_short
    )
    if path.exists():
        raise FileExistsError(
            f"Digest artifact already exists: {path.name} — refusing to overwrite"
        )
    if isinstance(content, bytes):
        return atomic_write_bytes(path, content)
    return atomic_write_text(path, content)

"""Transactional latest-output publication."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from rollup.fsutil import publish_file_set
from rollup.run_context import RunStatus

logger = logging.getLogger(__name__)

LATEST_MD = "latest.md"
LATEST_HTML = "latest.html"


@dataclass(frozen=True)
class PublicationResult:
    dated_outputs_written: bool
    latest_outputs_updated: bool
    latest_md: Path | None = None
    latest_html: Path | None = None


def should_update_latest(
    run_status: RunStatus,
    *,
    publish_latest: bool,
    allow_partial_latest: bool,
) -> bool:
    if not publish_latest:
        return False
    if run_status == "success":
        return True
    if run_status == "partial" and allow_partial_latest:
        return True
    return False


def publish_latest_outputs(
    *,
    output_dir: Path,
    md_path: Path,
    html_path: Path,
    run_status: RunStatus,
    publish_latest: bool,
    allow_partial_latest: bool = False,
) -> PublicationResult:
    """Atomically publish latest.md and latest.html as a set when allowed.

    Partial/failed runs do not replace last-known-good latest digests by default.
    """
    output_dir = Path(output_dir)
    if not md_path.exists() or not html_path.exists():
        raise FileNotFoundError("Digest outputs missing; refusing to publish latest")
    if md_path.stat().st_size == 0 or html_path.stat().st_size == 0:
        raise ValueError("Digest outputs empty; refusing to publish latest")

    if not should_update_latest(
        run_status,
        publish_latest=publish_latest,
        allow_partial_latest=allow_partial_latest,
    ):
        logger.info(
            "Skipping latest.* update (status=%s publish_latest=%s allow_partial=%s)",
            run_status,
            publish_latest,
            allow_partial_latest,
        )
        return PublicationResult(
            dated_outputs_written=True,
            latest_outputs_updated=False,
        )

    latest_md = output_dir / LATEST_MD
    latest_html = output_dir / LATEST_HTML
    publish_file_set(
        [
            (md_path, latest_md),
            (html_path, latest_html),
        ]
    )
    return PublicationResult(
        dated_outputs_written=True,
        latest_outputs_updated=True,
        latest_md=latest_md,
        latest_html=latest_html,
    )

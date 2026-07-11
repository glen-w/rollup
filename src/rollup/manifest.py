"""Run manifests: schema v1, allowlist serialization, failure-safe writes."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rollup import __version__
from rollup.config import Config
from rollup.fsutil import atomic_write_text
from rollup.models import DigestReport, DigestStats
from rollup.run_context import RunContext, RunStatus
from rollup.run_options import GroupingConfig, ManifestConfig, RunOptions

if TYPE_CHECKING:
    from rollup.pipeline import AggregatedResults

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "started_at",
        "completed_at",
        "status",
        "mode",
        "rollup_version",
        "counts",
        "outputs_published",
        "latest_outputs_updated",
    }
)

STATUS_ENUM = frozenset({"success", "partial", "failure", "dry_run"})
MODE_ENUM = frozenset({"manual", "cron"})

# Top-level keys allowed in serialized manifests (privacy allowlist).
MANIFEST_TOP_LEVEL_ALLOWLIST = frozenset(
    {
        "schema_version",
        "run_id",
        "started_at",
        "completed_at",
        "status",
        "mode",
        "rollup_version",
        "config_fingerprint",
        "paths",
        "window",
        "counts",
        "classification_counts",
        "summary_source_counts",
        "grouping_counts",
        "ollama_enabled",
        "models_used",
        "outputs",
        "outputs_published",
        "latest_outputs_updated",
        "previous_successful_run_id",
        "warnings",
        "errors",
        "parse_error_summary",
    }
)

COUNTS_ALLOWLIST = frozenset(
    {
        "folders_scanned",
        "messages_seen",
        "messages_parsed",
        "messages_included",
        "messages_skipped_outside_window",
        "messages_skipped_seen_undated",
        "messages_deduped",
        "parse_fatal_errors",
        "parse_anomalies",
        "groups_created",
        "messages_in_groups",
        "standalone_cards",
    }
)


class ManifestValidationError(ValueError):
    """Raised when a manifest fails the schema contract."""


def config_fingerprint(
    config: Config,
    run_options: RunOptions,
    grouping: GroupingConfig,
) -> str:
    """SHA256 of stable config inputs (excludes presentation/volatile flags)."""
    payload = {
        "root": str(config.root),
        "mail_root": str(config.mail_root),
        "lookback_days": config.lookback_days,
        "folders_include": list(config.folders_include),
        "folders_exclude": list(config.folders_exclude),
        "ollama_enabled": not config.no_ollama,
        "ollama_model": config.ollama_model if not config.no_ollama else None,
        "summary_profile": config.summary_profile,
        "summary_profile_set_path": config.summary_profile_set_path,
        "grouping_enabled": grouping.enabled,
        "grouping_min_size": grouping.min_group_size,
        "mode": run_options.mode,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_manifest(data: dict[str, Any]) -> None:
    """Validate required fields, enums, and numeric invariants."""
    if not isinstance(data, dict):
        raise ManifestValidationError("Manifest must be a JSON object")

    missing = REQUIRED_FIELDS - set(data)
    if missing:
        raise ManifestValidationError(
            f"Missing required fields: {', '.join(sorted(missing))}"
        )

    version = data["schema_version"]
    if not isinstance(version, int):
        raise ManifestValidationError("schema_version must be an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        if version > MANIFEST_SCHEMA_VERSION:
            raise ManifestValidationError(
                f"Unsupported newer manifest schema_version={version} "
                f"(max supported={MANIFEST_SCHEMA_VERSION})"
            )
        raise ManifestValidationError(f"Unsupported schema_version={version}")

    if data["status"] not in STATUS_ENUM:
        raise ManifestValidationError(f"Invalid status: {data['status']}")
    if data["mode"] not in MODE_ENUM:
        raise ManifestValidationError(f"Invalid mode: {data['mode']}")

    for key in ("outputs_published", "latest_outputs_updated"):
        if not isinstance(data[key], bool):
            raise ManifestValidationError(f"{key} must be a boolean")

    counts = data["counts"]
    if not isinstance(counts, dict):
        raise ManifestValidationError("counts must be an object")
    for key, value in counts.items():
        if not isinstance(value, int) or value < 0:
            raise ManifestValidationError(f"counts.{key} must be a non-negative integer")

    seen = counts.get("messages_seen", 0)
    parsed = counts.get("messages_parsed", 0)
    included = counts.get("messages_included", 0)
    if parsed > seen:
        raise ManifestValidationError("messages_parsed must be <= messages_seen")
    if included > parsed:
        raise ManifestValidationError("messages_included must be <= messages_parsed")


def filter_allowlisted(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy containing only allowlisted top-level keys."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key not in MANIFEST_TOP_LEVEL_ALLOWLIST:
            continue
        if key == "counts" and isinstance(value, dict):
            out[key] = {k: v for k, v in value.items() if k in COUNTS_ALLOWLIST}
        else:
            out[key] = value
    return out


def read_manifest(path: Path) -> dict[str, Any]:
    """Read a manifest JSON file; ignore unknown fields; validate known contract."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_manifest(data)
    return data


def read_latest_manifest(manifest_dir: Path) -> dict[str, Any] | None:
    latest = Path(manifest_dir) / "latest.json"
    if not latest.exists():
        return None
    try:
        return read_manifest(latest)
    except (OSError, json.JSONDecodeError, ManifestValidationError) as exc:
        logger.warning("Could not read latest manifest: %s", exc)
        return None


def _relative_path(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve()))
    except ValueError:
        return Path(path).name


@dataclass
class ManifestBuilder:
    """Accumulate run metadata and write a validated allowlisted manifest."""

    ctx: RunContext
    config: Config
    run_options: RunOptions
    grouping: GroupingConfig
    manifest_config: ManifestConfig
    window_start: datetime
    window_end: datetime
    status: RunStatus | None = None
    completed_at: datetime | None = None
    outputs_published: bool = False
    latest_outputs_updated: bool = False
    previous_successful_run_id: str | None = None
    md_path: Path | None = None
    html_path: Path | None = None
    failure_errors: list[dict[str, str]] = field(default_factory=list)
    aggregated: AggregatedResults | None = None
    stats: DigestStats | None = None
    report: DigestReport | None = None
    _finalized: bool = False
    _payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        prior = read_latest_manifest(self.manifest_config.manifest_dir)
        if prior and prior.get("status") == "success":
            self.previous_successful_run_id = prior.get("run_id")

    def record_failure(self, exc: BaseException) -> None:
        self.failure_errors.append(
            {
                "code": type(exc).__name__,
                "message": str(exc)[:500],
            }
        )

    def set_outputs(
        self,
        *,
        md_path: Path | None,
        html_path: Path | None,
        outputs_published: bool,
        latest_outputs_updated: bool,
    ) -> None:
        self.md_path = md_path
        self.html_path = html_path
        self.outputs_published = outputs_published
        self.latest_outputs_updated = latest_outputs_updated

    def finalize(
        self,
        *,
        status: RunStatus,
        aggregated: AggregatedResults | None = None,
        stats: DigestStats | None = None,
        report: DigestReport | None = None,
    ) -> None:
        self.status = status
        self.completed_at = datetime.now().astimezone()
        if aggregated is not None:
            self.aggregated = aggregated
        if stats is not None:
            self.stats = stats
        if report is not None:
            self.report = report
        self._payload = self._build_payload()
        validate_manifest(self._payload)
        self._finalized = True

    def _build_payload(self) -> dict[str, Any]:
        agg = self.aggregated
        stats = self.stats
        parse = agg.parse if agg else None
        filt = agg.filter if agg else None
        grouping_meta = self.report.grouping_metadata if self.report else None

        counts = {
            "folders_scanned": (
                len(agg.discovery.folders) if agg and agg.discovery else 0
            ),
            "messages_seen": parse.counts.messages_seen if parse else 0,
            "messages_parsed": parse.counts.messages_parsed if parse else 0,
            "messages_included": (
                (stats.dated_included + stats.undated_needing_review) if stats else 0
            ),
            "messages_skipped_outside_window": (
                filt.counts.skipped_outside_window if filt else 0
            ),
            "messages_skipped_seen_undated": (
                filt.counts.skipped_seen_undated if filt else 0
            ),
            "messages_deduped": filt.counts.deduped_messages if filt else 0,
            "parse_fatal_errors": parse.counts.parse_fatal_errors if parse else 0,
            "parse_anomalies": parse.counts.parse_anomalies if parse else 0,
            "groups_created": grouping_meta.groups_created if grouping_meta else 0,
            "messages_in_groups": (
                grouping_meta.messages_in_groups if grouping_meta else 0
            ),
            "standalone_cards": grouping_meta.standalone_cards if grouping_meta else 0,
        }

        classification_counts: dict[str, int] = {}
        summary_source_counts: dict[str, int] = {}
        if self.report:
            for items in self.report.dated_by_folder.values():
                for item in items:
                    entries = (
                        item.entries if hasattr(item, "entries") else (item,)
                    )
                    for entry in entries:
                        ntype = entry.classified.newsletter_type
                        classification_counts[ntype] = (
                            classification_counts.get(ntype, 0) + 1
                        )
                        src = entry.summary_source
                        summary_source_counts[src] = (
                            summary_source_counts.get(src, 0) + 1
                        )
            for item in self.report.undated:
                entries = item.entries if hasattr(item, "entries") else (item,)
                for entry in entries:
                    ntype = entry.classified.newsletter_type
                    classification_counts[ntype] = (
                        classification_counts.get(ntype, 0) + 1
                    )
                    src = entry.summary_source
                    summary_source_counts[src] = summary_source_counts.get(src, 0) + 1

        warnings: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = list(self.failure_errors)
        parse_error_summary: list[dict[str, Any]] = []
        if parse:
            for err in parse.errors:
                parse_error_summary.append(
                    {
                        "kind": err.code,
                        "folder": err.folder,
                        "count": 1,
                    }
                )
            for warn in parse.warnings:
                warnings.append(
                    {"code": warn.code, "count": warn.count, "folder": warn.folder}
                )

        models_used: list[str] = []
        if agg and agg.summarize and agg.summarize.summary_metadata:
            models_used = list(agg.summarize.summary_metadata.models_used)

        log_file = None
        if not self.run_options.dry_run:
            log_file = f"rollup-{self.ctx.run_start_time.strftime('%Y-%m-%d')}.log"

        payload: dict[str, Any] = {
            "schema_version": self.manifest_config.schema_version,
            "run_id": self.ctx.run_id,
            "started_at": self.ctx.run_start_time.isoformat(),
            "completed_at": (
                self.completed_at or datetime.now().astimezone()
            ).isoformat(),
            "status": self.status or "failure",
            "mode": self.ctx.mode,
            "rollup_version": __version__,
            "config_fingerprint": config_fingerprint(
                self.config, self.run_options, self.grouping
            ),
            "paths": {
                "root": str(self.config.root),
                "mail_root": str(self.config.mail_root),
                "output_dir": str(self.config.output_dir),
                "state_dir": str(self.config.state_dir),
                "log_dir": str(self.config.log_dir),
                "log_file": log_file,
            },
            "window": {
                "lookback_days": self.config.lookback_days,
                "start": self.window_start.isoformat(),
                "end": self.window_end.isoformat(),
            },
            "counts": counts,
            "classification_counts": classification_counts,
            "summary_source_counts": summary_source_counts,
            "grouping_counts": (
                grouping_meta.grouping_counts if grouping_meta else {}
            ),
            "ollama_enabled": not self.config.no_ollama,
            "models_used": models_used,
            "outputs": {
                "markdown": _relative_path(self.md_path, self.config.output_dir),
                "html": _relative_path(self.html_path, self.config.output_dir),
            },
            "outputs_published": self.outputs_published,
            "latest_outputs_updated": self.latest_outputs_updated,
            "previous_successful_run_id": self.previous_successful_run_id,
            "warnings": warnings,
            "errors": errors,
            "parse_error_summary": parse_error_summary,
        }
        return filter_allowlisted(payload)

    def write_if_state_writable(self, *, update_latest: bool = False) -> Path | None:
        """Write the finalized manifest if state_dir is writable.

        Never updates latest.json unless the payload is fully validated and
        update_latest is True (success + latest outputs published).
        """
        if not self._finalized or self._payload is None:
            # Attempt a minimal failure payload if we never finalized.
            self.finalize(status="failure", aggregated=self.aggregated)
        assert self._payload is not None
        validate_manifest(self._payload)

        manifest_dir = Path(self.manifest_config.manifest_dir)
        try:
            manifest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Cannot create manifest dir: {exc}") from exc

        started = self.ctx.run_start_time.astimezone(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H-%M-%SZ")
        filename = f"{started}-{self.ctx.run_id_short}.json"
        path = manifest_dir / filename
        atomic_write_text(path, json.dumps(self._payload, indent=2) + "\n")

        if update_latest and self._payload.get("status") == "success":
            latest = manifest_dir / "latest.json"
            atomic_write_text(latest, json.dumps(self._payload, indent=2) + "\n")

        return path

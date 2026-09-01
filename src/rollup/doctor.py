"""Configuration-aware diagnostics — not a second pipeline."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from rollup import __version__
from rollup.config import Config
from rollup.discovery import iter_mbox_files
from rollup.effort import (
    apply_single_model_to_preset,
    get_effort_preset,
    resolve_effort_name,
)
from rollup.manifest import read_latest_manifest
from rollup.run_options import RunOptions
from rollup.safety import SafetyError, assert_safe_write_paths, is_inside, validate_read_root

logger = logging.getLogger(__name__)

CheckStatus = Literal["pass", "warn", "fail", "info"]
OLLAMA_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class DoctorCheck:
    id: str
    status: CheckStatus
    message: str
    fix: str = ""


@dataclass(frozen=True)
class DoctorReport:
    schema_version: int
    ok: bool
    error_count: int
    warning_count: int
    checks: tuple[DoctorCheck, ...]


def _check_python() -> DoctorCheck:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        return DoctorCheck(
            id="python_version",
            status="fail",
            message=f"Python {major}.{minor} is below 3.10",
            fix="Upgrade to Python 3.10 or newer",
        )
    return DoctorCheck(
        id="python_version",
        status="pass",
        message=f"Python {major}.{minor}.{sys.version_info[2]}",
    )


def _check_package() -> DoctorCheck:
    return DoctorCheck(
        id="package_version",
        status="info",
        message=f"Package rollup {__version__}",
    )


def _check_path_exists(check_id: str, path: Path, label: str) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(
            id=check_id,
            status="fail",
            message=f"{label} does not exist: {path}",
            fix=f"Create the directory or fix --{check_id.replace('_exists', '').replace('_', '-')}",
        )
    if not path.is_dir():
        return DoctorCheck(
            id=check_id,
            status="fail",
            message=f"{label} is not a directory: {path}",
            fix=f"Point --{check_id.replace('_', '-')} at a directory",
        )
    return DoctorCheck(
        id=check_id,
        status="pass",
        message=f"{label} exists",
    )


def _check_writable(check_id: str, path: Path, label: str) -> DoctorCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".rollup-doctor-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return DoctorCheck(
            id=check_id,
            status="fail",
            message=f"{label} not writable: {exc}",
            fix=f"Fix permissions on {path}",
        )
    return DoctorCheck(
        id=check_id,
        status="pass",
        message=f"{label} is writable",
    )


def _check_safe_paths(config: Config) -> DoctorCheck:
    try:
        assert_safe_write_paths(
            config.mail_root,
            config.output_dir,
            config.state_dir,
            config.log_dir,
            config.db_path,
        )
    except SafetyError as exc:
        return DoctorCheck(
            id="safe_write_paths",
            status="fail",
            message=str(exc),
            fix="Move output/state/log dirs outside the mail root",
        )
    return DoctorCheck(
        id="safe_write_paths",
        status="pass",
        message="Writable paths are outside the mail root",
    )


def _check_mbox_discoverable(config: Config) -> DoctorCheck:
    folders = list(iter_mbox_files(config.root))
    if not folders:
        return DoctorCheck(
            id="mbox_discoverable",
            status="warn",
            message="No mbox-like files discovered under --root",
            fix="Check --root points at a Thunderbird .sbd tree",
        )
    return DoctorCheck(
        id="mbox_discoverable",
        status="pass",
        message=f"Discovered {len(folders)} mbox folder(s)",
    )


def _check_msf_ignored(config: Config) -> DoctorCheck:
    folders = list(iter_mbox_files(config.root))
    bad = [f for f in folders if str(f.mbox_path).endswith(".msf")]
    if bad:
        return DoctorCheck(
            id="msf_ignored",
            status="fail",
            message=f"Discovery returned .msf paths ({len(bad)})",
            fix="Report a bug — .msf index files must be ignored",
        )
    return DoctorCheck(
        id="msf_ignored",
        status="pass",
        message=".msf index files are ignored",
    )


def _check_sqlite(config: Config) -> DoctorCheck:
    try:
        from rollup.state import init_db

        config.state_dir.mkdir(parents=True, exist_ok=True)
        conn = init_db(config.db_path)
        conn.close()
    except Exception as exc:
        return DoctorCheck(
            id="sqlite_state",
            status="fail",
            message=f"SQLite state DB failed: {exc}",
            fix=f"Fix permissions or remove a corrupt {config.db_path}",
        )
    return DoctorCheck(
        id="sqlite_state",
        status="pass",
        message="SQLite state DB can be opened",
    )


def _check_source_registry(config: Config) -> list[DoctorCheck]:
    try:
        from rollup.source_doctor import run_source_doctor
        from rollup.state import init_db

        conn = init_db(config.db_path)
        try:
            report = run_source_doctor(conn)
        finally:
            conn.close()
    except Exception as exc:
        return [
            DoctorCheck(
                id="source_registry",
                status="fail",
                message=f"Source registry doctor failed: {exc}",
                fix="Run rollup sources doctor after fixing state DB permissions",
            )
        ]
    out: list[DoctorCheck] = []
    for check in report.get("checks", []):
        status = check.get("status", "info")
        if status not in ("pass", "warn", "fail", "info"):
            status = "info"
        out.append(
            DoctorCheck(
                id=str(check.get("id", "source_registry")),
                status=status,  # type: ignore[arg-type]
                message=str(check.get("message", "")),
            )
        )
    return out


def _check_web_index(config: Config) -> list[DoctorCheck]:
    """Compare latest successful outputs vs rollup_runs entry index."""
    try:
        from rollup.state import init_db

        conn = init_db(config.db_path)
        try:
            row = conn.execute(
                """SELECT run_id, entry_index_version, markdown_relpath, status
                   FROM rollup_runs
                   ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rollup_runs'"
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return [
            DoctorCheck(
                id="web_index",
                status="warn",
                message=f"Could not inspect web index: {exc}",
            )
        ]
    if tables is None:
        return [
            DoctorCheck(
                id="web_index",
                status="info",
                message="Web index tables not present (unexpected on schema v8+)",
            )
        ]
    if row is None:
        return [
            DoctorCheck(
                id="web_index",
                status="info",
                message="No indexed rollup runs yet",
            )
        ]
    run_id, entry_ver, md_rel, status = row
    if int(entry_ver or 0) <= 0:
        return [
            DoctorCheck(
                id="web_index",
                status="warn",
                message=(
                    f"Latest indexed run {run_id} has no entry index "
                    f"(status={status}); run a digest or rollup web reindex"
                ),
                fix="rollup digest … then browse with rollup web",
            )
        ]
    if md_rel:
        from rollup.output_archive import resolve_output_artifact

        path = resolve_output_artifact(config.output_dir, md_rel)
        if path is None:
            return [
                DoctorCheck(
                    id="web_index",
                    status="warn",
                    message=f"Indexed markdown missing on disk for run {run_id}: {md_rel}",
                )
            ]
    return [
        DoctorCheck(
            id="web_index",
            status="pass",
            message=f"Latest run {run_id} has entry index v{entry_ver}",
        )
    ]


def _check_last_manifest(config: Config) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    manifest_dir = config.state_dir / "manifests"
    latest = read_latest_manifest(manifest_dir)
    if latest is None:
        checks.append(
            DoctorCheck(
                id="last_manifest",
                status="info",
                message="No previous run manifest found",
                fix="Run a non-dry-run digest to create one",
            )
        )
        return checks
    checks.append(
        DoctorCheck(
            id="last_manifest",
            status="pass",
            message=f"Latest manifest present (run_id={latest.get('run_id', '?')[:8]}…)",
        )
    )
    status = latest.get("status", "unknown")
    completed = latest.get("completed_at", "?")
    level: CheckStatus = "pass" if status == "success" else "warn"
    checks.append(
        DoctorCheck(
            id="last_run_status",
            status=level,
            message=f"Last run: {status} ({completed})",
            fix="Inspect state/manifests/latest.json if status is not success",
        )
    )
    return checks


def _check_live_mail(config: Config) -> DoctorCheck | None:
    try:
        warnings = validate_read_root(
            config.root,
            config.mail_root,
            config.output_dir,
            config.state_dir,
            config.log_dir,
        )
    except SafetyError as exc:
        return DoctorCheck(
            id="root_valid",
            status="fail",
            message=str(exc),
            fix="Fix --root / path configuration",
        )
    if warnings:
        return DoctorCheck(
            id="live_mail_warning",
            status="warn",
            message="Reading live Thunderbird data",
            fix="Test with tests/fixtures/Newsletters.sbd first",
        )
    return None


def _effective_effort_preset(config: Config):
    name = resolve_effort_name(config.effort)
    preset = get_effort_preset(
        name, override=config.effort_overrides.get(name)
    )
    if config.single_model:
        preset = apply_single_model_to_preset(
            preset, config.single_model, provider=config.llm_provider
        )
    return preset


def _check_effort_preset(config: Config) -> DoctorCheck:
    preset = _effective_effort_preset(config)
    models = ", ".join(preset.expected_models())
    return DoctorCheck(
        id="effort_preset",
        status="info",
        message=(
            f"Effort {preset.name}: ollama_model={preset.ollama_model}, "
            f"final_review_model={preset.final_review_model}, "
            f"max_chars_for_llm={preset.max_chars_for_llm}, "
            f"models=[{models}]"
        ),
    )


def _model_appears_available(wanted: str, models: list[str]) -> bool:
    if any(
        wanted == m or m.startswith(wanted + ":") or wanted.startswith(m.split(":")[0])
        for m in models
    ):
        return True
    return any(wanted in m for m in models)


def _check_ollama_loopback(config: Config, run_options: RunOptions) -> DoctorCheck | None:
    if config.no_ollama:
        return None
    parsed = urlparse(config.ollama_url)
    host = (parsed.hostname or "").lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if loopback or config.allow_remote_ollama:
        return DoctorCheck(
            id="ollama_loopback",
            status="pass",
            message="Ollama URL policy OK",
        )
    return DoctorCheck(
        id="ollama_loopback",
        status="fail",
        message=f"Ollama URL host is not loopback: {host}",
        fix="Use localhost or pass --allow-remote-ollama explicitly",
    )


def _check_litellm_config(config: Config) -> DoctorCheck | None:
    """Import/config readiness only — never issues a completion."""
    if config.no_ollama:
        return None
    uses_litellm = config.llm_provider == "litellm" or (
        config.final_review_enabled and config.final_review_provider == "litellm"
    )
    if not uses_litellm:
        return None
    try:
        import litellm  # noqa: F401
    except ImportError:
        return DoctorCheck(
            id="litellm_extra",
            status="fail",
            message="LiteLLM is not installed",
            fix="pip install 'rollup[llm]'",
        )
    model = config.llm_model or config.final_review_model
    msg = "LiteLLM extra installed; credentials appear configured / could not verify locally"
    if model:
        msg = f"{msg} (model={model})"
    return DoctorCheck(
        id="litellm_config",
        status="pass",
        message=msg,
    )


def _check_ollama_network(config: Config) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if config.no_ollama:
        return checks
    # Skip Ollama tags probe when the effective default provider is LiteLLM-only
    # and final review is also not on Ollama.
    ollama_needed = config.llm_provider == "ollama" or (
        config.final_review_enabled and config.final_review_provider == "ollama"
    )
    if not ollama_needed:
        return checks
    try:
        import requests
    except ImportError:
        checks.append(
            DoctorCheck(
                id="ollama_reachable",
                status="warn",
                message="requests not available for Ollama probe",
                fix="Install rollup with its dependencies",
            )
        )
        return checks

    parsed = urlparse(config.ollama_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    tags_url = f"{base}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=OLLAMA_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        checks.append(
            DoctorCheck(
                id="ollama_reachable",
                status="pass",
                message=f"Ollama reachable ({len(models)} model(s))",
            )
        )
        wanted_models = list(_effective_effort_preset(config).expected_models())
        if config.ollama_model and config.ollama_model not in wanted_models:
            wanted_models.append(config.ollama_model)
        if config.final_review_model and config.final_review_model not in wanted_models:
            wanted_models.append(config.final_review_model)

        missing = [m for m in wanted_models if not _model_appears_available(m, models)]
        effort_name = resolve_effort_name(config.effort)
        if missing:
            checks.append(
                DoctorCheck(
                    id="ollama_models",
                    status="warn",
                    message=(
                        f"Effort {effort_name} models missing from /api/tags: "
                        + ", ".join(missing)
                    ),
                    fix="; ".join(f"ollama pull {m}" for m in missing),
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    id="ollama_models",
                    status="pass",
                    message=(
                        f"Effort {effort_name} models appear available: "
                        + ", ".join(wanted_models)
                    ),
                )
            )
    except Exception as exc:
        checks.append(
            DoctorCheck(
                id="ollama_reachable",
                status="warn",
                message=f"Ollama not reachable: {exc}",
                fix="Start Ollama or omit --ollama (preview summaries will be used)",
            )
        )
    return checks


def _check_full_sample(config: Config) -> list[DoctorCheck]:
    """Expensive read-only sample parse of up to 3 folders."""
    from rollup.parse import parse_mbox_folder

    checks: list[DoctorCheck] = []
    folders = list(iter_mbox_files(config.root))[:3]
    if not folders:
        return checks
    parsed_total = 0
    for folder in folders:
        before = folder.mbox_path.stat().st_mtime_ns if folder.mbox_path.exists() else 0
        msgs, errors, folder_errors = parse_mbox_folder(
            folder, config.max_body_chars, config.max_display_links
        )
        after = folder.mbox_path.stat().st_mtime_ns if folder.mbox_path.exists() else 0
        if folder_errors:
            checks.append(
                DoctorCheck(
                    id="full_parse_sample",
                    status="warn",
                    message=f"Folder {folder.folder_name}: {folder_errors[0]}",
                    fix="Check mbox readability / Thunderbird sync",
                )
            )
            continue
        parsed_total += len(msgs)
        if before != after:
            checks.append(
                DoctorCheck(
                    id="mbox_mtime_stability",
                    status="warn",
                    message=f"mbox mtime changed while reading {folder.folder_name}",
                    fix="Avoid running while Thunderbird is syncing/compacting",
                )
            )
    checks.append(
        DoctorCheck(
            id="full_parse_sample",
            status="pass",
            message=f"Sample-parsed {parsed_total} message(s) from {len(folders)} folder(s)",
        )
    )
    # Ensure we did not write under mail root (probe for common junk names).
    if is_inside(config.output_dir, config.mail_root):
        checks.append(
            DoctorCheck(
                id="full_readonly",
                status="fail",
                message="output_dir resolves inside mail root",
                fix="Move --output-dir outside --mail-root",
            )
        )
    return checks


def _check_linkedin_session(config: Config) -> DoctorCheck | None:
    """Warn when LinkedIn is enabled but session cookies are missing."""
    if not config.linkedin_enabled or not config.linkedin.enabled:
        return None
    if not config.linkedin.searches:
        return None
    from rollup.env_file import default_env_file_path, resolve_env_file_paths
    from rollup.linkedin.session import (
        JSESSIONID_ENV,
        LI_AT_ENV,
        jsession_id_configured,
        linkedin_cookie_configured,
    )

    env_path = default_env_file_path()
    has_file = any(p.expanduser().is_file() for p in resolve_env_file_paths())
    li_at = linkedin_cookie_configured()
    jsession = jsession_id_configured()
    if li_at and jsession:
        source = f" (from {env_path})" if has_file else ""
        return DoctorCheck(
            id="linkedin_session",
            status="pass",
            message=f"LinkedIn session cookies configured{source}",
        )
    missing: list[str] = []
    if not li_at:
        missing.append(LI_AT_ENV)
    if not jsession:
        missing.append(JSESSIONID_ENV)
    fix = (
        f"Set {', '.join(missing)} in the environment or in "
        f"{env_path} (chmod 600; never TOML). "
        "See docs/CONFIG.md#session-cookies-environment-only."
    )
    if has_file:
        fix = (
            f"Update {env_path} with {', '.join(missing)} "
            "(refresh both cookies together after 401). "
            "See docs/CONFIG.md#session-cookies-environment-only."
        )
    return DoctorCheck(
        id="linkedin_session",
        status="warn",
        message=f"LinkedIn enabled but missing: {', '.join(missing)}",
        fix=fix,
    )


def _check_mail_path_discovery(config: Config) -> DoctorCheck:
    """Advise when defaults are missing and Thunderbird discovery finds candidates."""
    from rollup.config import DEFAULT_NEWSLETTER_ROOT
    from rollup.paths import discover_newsletters_sbd

    root = config.root.expanduser()
    if root.is_dir():
        return DoctorCheck(
            id="mail_path_discovery",
            status="pass",
            message=f"Newsletter root exists: {root}",
        )

    candidates = discover_newsletters_sbd()
    default_root = DEFAULT_NEWSLETTER_ROOT.expanduser()
    if candidates:
        listed = ", ".join(str(p) for p in candidates)
        return DoctorCheck(
            id="mail_path_discovery",
            status="warn",
            message=(
                f"Newsletter root missing ({root}); "
                f"Thunderbird candidates: {listed}"
            ),
            fix=(
                "Set root/mail_root in ~/.config/rollup/config.toml "
                "or pass --root / --mail-root"
            ),
        )
    return DoctorCheck(
        id="mail_path_discovery",
        status="fail" if root == default_root or not root.exists() else "warn",
        message=f"Newsletter root missing and no Newsletters.sbd discovered: {root}",
        fix=(
            "Point --root at your Thunderbird Newsletters.sbd, or set root in "
            "~/.config/rollup/config.toml"
        ),
    )


def run_doctor(
    config: Config,
    run_options: RunOptions,
    *,
    full: bool = False,
    network: bool = False,
) -> DoctorReport:
    checks: list[DoctorCheck] = [
        _check_python(),
        _check_package(),
        _check_effort_preset(config),
        _check_path_exists("root_exists", config.root, "Newsletter root"),
        _check_path_exists("mail_root_exists", config.mail_root, "Mail root"),
        _check_mail_path_discovery(config),
        _check_safe_paths(config),
        _check_writable("output_writable", config.output_dir, "output_dir"),
        _check_writable("state_writable", config.state_dir, "state_dir"),
        _check_writable("log_writable", config.log_dir, "log_dir"),
    ]

    live = _check_live_mail(config)
    if live:
        checks.append(live)

    if config.root.exists() and config.root.is_dir():
        checks.append(_check_mbox_discoverable(config))
        checks.append(_check_msf_ignored(config))

    checks.append(_check_sqlite(config))
    checks.extend(_check_source_registry(config))
    checks.extend(_check_web_index(config))
    checks.extend(_check_last_manifest(config))

    loopback = _check_ollama_loopback(config, run_options)
    if loopback:
        checks.append(loopback)

    litellm_check = _check_litellm_config(config)
    if litellm_check:
        checks.append(litellm_check)

    linkedin_check = _check_linkedin_session(config)
    if linkedin_check:
        checks.append(linkedin_check)

    do_network = network or (not config.no_ollama)
    if do_network:
        checks.extend(_check_ollama_network(config))

    if full and config.root.exists():
        checks.extend(_check_full_sample(config))

    error_count = sum(1 for c in checks if c.status == "fail")
    warning_count = sum(1 for c in checks if c.status == "warn")
    return DoctorReport(
        schema_version=1,
        ok=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
        checks=tuple(checks),
    )


def format_doctor_human(report: DoctorReport) -> str:
    symbols = {"pass": "✓", "warn": "⚠", "fail": "✗", "info": "•"}
    lines = ["rollup doctor", "──────────────"]
    for check in report.checks:
        sym = symbols.get(check.status, "•")
        line = f"{sym} {check.message}"
        if check.fix and check.status in {"fail", "warn"}:
            line += f" (fix: {check.fix})"
        lines.append(line)
    lines.append("")
    lines.append(
        f"{report.error_count} error(s), {report.warning_count} warning(s)"
    )
    return "\n".join(lines)


def format_doctor_json(report: DoctorReport) -> str:
    payload = {
        "schema_version": report.schema_version,
        "ok": report.ok,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "checks": [asdict(c) for c in report.checks],
    }
    return json.dumps(payload, indent=2) + "\n"

"""Configuration defaults and window computation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path


DEFAULT_MAIL_ROOT = Path("/Users/89298/email/gmail")
DEFAULT_NEWSLETTER_ROOT = DEFAULT_MAIL_ROOT / "Newsletters.sbd"
DEFAULT_OUTPUT_DIR = Path("./output")
DEFAULT_STATE_DIR = Path("./state")
DEFAULT_LOG_DIR = Path("./logs")
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MAX_BODY_CHARS = 200_000
DEFAULT_MAX_CHARS_FOR_LLM = 30_000
DEFAULT_MAX_DISPLAY_LINKS = 8
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"


@dataclass(frozen=True)
class Config:
    root: Path
    mail_root: Path
    output_dir: Path
    state_dir: Path
    log_dir: Path
    lookback_days: int
    folders_include: tuple[str, ...]
    folders_exclude: tuple[str, ...]
    dry_run: bool
    no_ollama: bool
    include_seen_undated: bool
    rebuild_summaries: bool
    max_body_chars: int
    max_chars_for_llm: int
    max_display_links: int
    ollama_url: str
    ollama_model: str
    allow_remote_ollama: bool
    summary_profile: str | None
    summary_variants: tuple[str, ...]
    summary_type_routing: bool
    summary_profile_set_path: str | None
    export_summary_profile_set_path: str | None
    list_summary_profiles: bool
    list_newsletter_types: bool
    summary_routing_report: bool
    verbose: bool
    quiet: bool

    @property
    def db_path(self) -> Path:
        return self.state_dir / "rollup.db"


def compute_date_window(
    generated_at: datetime, lookback_days: int
) -> tuple[datetime, datetime]:
    """Inclusive calendar-day window in local timezone."""
    if generated_at.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        generated_at = generated_at.replace(tzinfo=local_tz)
    local_date = generated_at.date()
    window_end = datetime.combine(
        local_date, time(23, 59, 59, 999999), generated_at.tzinfo
    )
    start_date = local_date - timedelta(days=lookback_days - 1)
    window_start = datetime.combine(start_date, time.min, generated_at.tzinfo)
    return window_start, window_end

"""Configuration defaults and window computation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from rollup.linkedin.config import LinkedInConfig
from rollup.reddit.config import RedditConfig

if TYPE_CHECKING:
    from rollup.effort import EffortModelOverride
    from rollup.folder_theme import FolderThemeOverride

DEFAULT_MAIL_ROOT = Path.home() / "email" / "gmail"
DEFAULT_NEWSLETTER_ROOT = DEFAULT_MAIL_ROOT / "Newsletters.sbd"
# Digests live outside the repo by default (override via --output-dir / config.toml).
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "rollup-outputs"
DEFAULT_STATE_DIR = Path("./state")
DEFAULT_LOG_DIR = Path("./logs")
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MAX_BODY_CHARS = 200_000
DEFAULT_MAX_CHARS_FOR_LLM = 30_000
DEFAULT_MAX_DISPLAY_LINKS = 8
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_FINAL_REVIEW_PROFILE = "strict"
DEFAULT_FINAL_REVIEW_PROVIDER = "ollama"
DEFAULT_LLM_PROVIDER = "ollama"
DEFAULT_FINAL_REVIEW_MODE = "report"
DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO = 0.08
DEFAULT_EFFORT = "balanced"
DEFAULT_RUN_PROFILE = "weekly"


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
    summary_type_routing: bool | None
    summary_profile_set_path: str | None
    export_summary_profile_set_path: str | None
    list_summary_profiles: bool
    list_newsletter_types: bool
    summary_routing_report: bool
    final_review_enabled: bool = False
    final_review_mode: str = DEFAULT_FINAL_REVIEW_MODE
    final_review_profile: str = DEFAULT_FINAL_REVIEW_PROFILE
    final_review_provider: str = DEFAULT_FINAL_REVIEW_PROVIDER
    final_review_model: str | None = None
    final_review_report_path: Path | None = None
    rebuild_final_review: bool = False
    final_review_preserve_links: bool = True
    final_review_preserve_quotes: bool = True
    final_review_max_changed_chars_ratio: float = (
        DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO
    )
    final_review_allow_cron_apply: bool = False
    final_review_apply_policy: str = "conservative"  # conservative|standard
    final_review_max_patches_unattended: int = 5
    final_review_max_changed_chars_unattended: int = 800
    group_summaries_enabled: bool = False
    max_group_summary_calls: int = 8
    group_summary_variant_policy: str = "primary"  # only "primary" accepted
    min_usable_member_summaries: int = 2
    effort: str | None = None
    list_efforts: bool = False
    run_profile: str | None = None
    list_profiles: bool = False
    folder_themes: dict[str, FolderThemeOverride] = field(default_factory=dict)
    effort_overrides: dict[str, EffortModelOverride] = field(default_factory=dict)
    single_model: str | None = None
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str | None = None
    llm_api_base: str | None = None
    no_linkedin: bool = True
    linkedin: LinkedInConfig = field(default_factory=LinkedInConfig)
    no_webpage: bool = False
    no_reddit: bool = True
    reddit: RedditConfig = field(default_factory=RedditConfig)
    reddit_refresh: bool = False
    linkedin_refresh: bool = False

    @property
    def db_path(self) -> Path:
        return self.state_dir / "rollup.db"

    @property
    def llm_enabled(self) -> bool:
        """True when LLM summarisation/review is enabled (--ollama / sticky ollama)."""
        return not self.no_ollama

    @property
    def linkedin_enabled(self) -> bool:
        """True when LinkedIn content-search fetch is enabled (--linkedin / [linkedin].enabled)."""
        return not self.no_linkedin

    @property
    def webpage_enabled(self) -> bool:
        """True when webpage queue ingest is enabled (default; pass --no-webpage to skip)."""
        return not self.no_webpage

    @property
    def reddit_enabled(self) -> bool:
        """True when Reddit fetch is enabled (--reddit / [reddit].enabled)."""
        return not self.no_reddit


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

"""Google Scholar alert mail lane (optional detailed paper summaries)."""

from rollup.scholar.config import ScholarConfig, parse_scholar_config
from rollup.scholar.detect import PAPER_MESSAGE_KEY_PREFIX, is_scholar_alert
from rollup.scholar.parse import ScholarPaper, extract_papers_from_message

__all__ = [
    "PAPER_MESSAGE_KEY_PREFIX",
    "ScholarConfig",
    "ScholarPaper",
    "extract_papers_from_message",
    "is_scholar_alert",
    "parse_scholar_config",
]

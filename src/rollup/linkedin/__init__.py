"""LinkedIn content-search ingestion (optional, opt-in network)."""

from rollup.linkedin.config import LinkedInConfig, LinkedInSearch, folder_name_for_search
from rollup.linkedin.models import LinkedInPost
from rollup.linkedin.parse import linkedin_post_to_parsed_message

__all__ = [
    "LinkedInConfig",
    "LinkedInPost",
    "LinkedInSearch",
    "folder_name_for_search",
    "linkedin_post_to_parsed_message",
]

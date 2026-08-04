"""Locked run status, exit-code, and publication-boundary contracts.

See docs/CRON.md and docs/CONTRACT.md. These definitions are authoritative for
integrity work; pipeline status derivation must stay aligned with this matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Exit codes (CLI / DigestRunResult)
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PARTIAL = 2

FailureClass = Literal[
    "no_input",
    "empty_window",
    "mbox_mutation",
    "required_publication",
    "optional_writer",
    "latest_publication",
    "manifest",
    "web_index",
    "seen_state",
]

# Maps failure class → (status when this class is the primary outcome, exit).
# Secondary degradations (web_index) do not alone force partial.
FAILURE_EXIT: dict[FailureClass, tuple[Literal["success", "partial", "failure"], int]] = {
    "no_input": ("failure", EXIT_FAILURE),
    "empty_window": ("success", EXIT_SUCCESS),
    "mbox_mutation": ("partial", EXIT_PARTIAL),
    "required_publication": ("failure", EXIT_FAILURE),
    "optional_writer": ("partial", EXIT_PARTIAL),
    "latest_publication": ("partial", EXIT_PARTIAL),
    "manifest": ("partial", EXIT_PARTIAL),
    "web_index": ("success", EXIT_SUCCESS),  # alone; compose with other failures
    "seen_state": ("partial", EXIT_PARTIAL),
}


@dataclass(frozen=True)
class WriterPlanEntry:
    """One enabled output writer with required/optional classification."""

    name: str
    required: bool = True


# Accounting fields that must be distinguishable for empty-window vs no-input.
EMPTY_RUN_COUNTERS = (
    "messages_discovered",
    "messages_parse_candidates",
    "messages_parsed_ok",
    "messages_parse_failed",
    "messages_in_date_window",
    "messages_filtered",
    "messages_included",
)

# Mbox mutation anomaly codes (identity/size/mtime checks).
MBOX_ANOMALY_DISAPPEARED = "mbox_disappeared"
MBOX_ANOMALY_REPLACED = "mbox_replaced"
MBOX_ANOMALY_SHRUNK = "mbox_shrunk"
MBOX_ANOMALY_GREW = "mbox_grew"
MBOX_ANOMALY_IDENTITY_WEAK = "mbox_identity_weak"

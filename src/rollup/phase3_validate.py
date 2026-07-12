"""Deprecated Phase-3 / hardening runtime config validation wrapper.

Used by CLI argparse paths and any config-file / programmatic Config builds
so invalid combinations fail the same way everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from rollup.config import Config
from rollup.effective_run import resolve_effective_run
from rollup.final_review_codes import ApplyPolicy
from rollup.run_options import GroupingConfig, RunOptions


@dataclass(frozen=True)
class ValidatedPhase3Runtime:
    """Result of central validation; carry resolved apply policy into the pipeline."""

    apply_policy: ApplyPolicy | None


def validate_phase3_runtime_config(
    config: Config,
    *,
    run_options: RunOptions | None = None,
    grouping: GroupingConfig | None = None,
) -> ValidatedPhase3Runtime:
    """Deprecated: call resolve_effective_run and return the legacy result."""
    eff = resolve_effective_run(
        config,
        run_options or RunOptions(),
        grouping=grouping,
    )
    return ValidatedPhase3Runtime(apply_policy=eff.apply_policy)

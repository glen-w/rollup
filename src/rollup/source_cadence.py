"""Deterministic cadence estimation from dated message samples."""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Sequence

from rollup.source_models import (
    CADENCE_INFERENCE_MIN_SAMPLES,
    CadenceEstimate,
    CadenceLabel,
)


def estimate_cadence(dates: Sequence[datetime]) -> CadenceEstimate:
    """Estimate publication cadence from dated message timestamps.

    ``sample_count`` is the number of dated **messages** (not intervals).
    Inference requires at least CADENCE_INFERENCE_MIN_SAMPLES messages.
    """
    aware = [d for d in dates if d is not None]
    aware = sorted(aware)
    sample_count = len(aware)
    if sample_count < CADENCE_INFERENCE_MIN_SAMPLES:
        return CadenceEstimate("unknown", 0.0, sample_count, None)

    intervals_hours: list[float] = []
    for i in range(1, len(aware)):
        delta = (aware[i] - aware[i - 1]).total_seconds() / 3600.0
        if delta <= 0:
            continue
        if delta > 90 * 24:
            continue
        intervals_hours.append(delta)

    if len(intervals_hours) < CADENCE_INFERENCE_MIN_SAMPLES - 1:
        return CadenceEstimate("unknown", 0.0, sample_count, None)

    med = float(median(intervals_hours))
    label = _label_for_median(med, intervals_hours)
    n_intervals = len(intervals_hours)
    stability = _stability(intervals_hours, med)
    confidence = min(1.0, n_intervals / 12.0) * stability
    confidence = max(0.0, min(1.0, confidence))
    return CadenceEstimate(label, confidence, sample_count, med)


def _label_for_median(med: float, intervals: list[float]) -> CadenceLabel:
    if _high_mad(intervals, med):
        return "irregular"
    if med < 6:
        return "realtime"
    if med <= 36:
        return "daily"
    if med <= 96:
        return "several_per_week"
    if med <= 216:
        return "weekly"
    return "irregular"


def _stability(intervals: list[float], med: float) -> float:
    if med <= 0 or not intervals:
        return 0.0
    sorted_i = sorted(intervals)
    q1 = sorted_i[len(sorted_i) // 4]
    q3 = sorted_i[(3 * len(sorted_i)) // 4]
    iqr = q3 - q1
    ratio = iqr / med
    if ratio <= 0.5:
        return 1.0
    if ratio >= 2.0:
        return 0.25
    return max(0.25, 1.0 - (ratio - 0.5) / 2.0)


def _high_mad(intervals: list[float], med: float) -> bool:
    if med <= 0:
        return True
    deviations = [abs(x - med) for x in intervals]
    mad = float(median(deviations)) if deviations else 0.0
    return (mad / med) > 1.5

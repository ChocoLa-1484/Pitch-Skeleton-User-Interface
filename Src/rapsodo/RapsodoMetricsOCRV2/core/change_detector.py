from __future__ import annotations

import logging

import Src.rapsodo.RapsodoMetricsOCRV2.config as config
from Src.rapsodo.RapsodoMetricsOCRV2.models import BaseMetrics

logger = logging.getLogger(__name__)


class ChangeDetector:
    """
    Determine whether a new entry (hit/pitch) has arrived.

    A new entry is detected when ALL non-None fields in the current metrics
    differ from the corresponding previous values by more than `tolerance`.
    If any field is None the reading is treated as "not new".

    Call reset() whenever the player changes so the first reading of the new
    player is always treated as new.
    """

    def __init__(self, tolerance: float = config.CHANGE_TOLERANCE) -> None:
        self._tolerance = tolerance
        self._previous: BaseMetrics | None = None

    def is_new_entry(self, metrics: BaseMetrics) -> bool:
        """Return True if `metrics` represents a new data entry."""
        values = metrics.field_values()

        # Any None field → skip this frame entirely
        if any(v is None for v in values.values()):
            logger.debug("Skipping metrics with None fields: %s", values)
            return False

        if self._previous is None:
            # First reading after startup or reset → always new
            self._previous = metrics
            return True

        prev_values = self._previous.field_values()

        # All non-None fields must differ by more than tolerance
        for key, current_val in values.items():
            prev_val = prev_values.get(key)
            if prev_val is None:
                # Previous value unknown → treat as changed
                continue
            try:
                if abs(float(current_val) - float(prev_val)) <= self._tolerance:
                    logger.debug(
                        "Field '%s' unchanged (prev=%.2f curr=%.2f tol=%.2f).",
                        key, prev_val, current_val, self._tolerance,
                    )
                    return False
            except (TypeError, ValueError):
                # Non-numeric fields: compare by equality
                if current_val == prev_val:
                    return False

        self._previous = metrics
        return True

    def reset(self) -> None:
        """Clear previous reading (e.g., on player change)."""
        self._previous = None
        logger.debug("ChangeDetector reset.")

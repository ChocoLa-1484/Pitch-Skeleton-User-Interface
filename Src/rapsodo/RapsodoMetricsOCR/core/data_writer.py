from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import Src.rapsodo.RapsodoMetricsOCR.config as config
from Src.rapsodo.RapsodoMetricsOCR.models import BaseMetrics
from Src.rapsodo.RapsodoMetricsOCR.modes.pitching import PitchMetrics

logger = logging.getLogger(__name__)


class DataWriter:
    """
    Persist metrics to a JSON file using atomic writes (temp-file + os.replace).

    The JSON schema includes a top-level session header and a growing list of
    entry dicts.  Each entry's field names are mode-specific (see SDD §3.7).
    """

    def __init__(self, output_path: str, mode: str, session_id: str) -> None:
        self._path = Path(output_path)
        self._mode = mode
        self._session_id = session_id
        self._current_player: str = ""
        self._entries: list[dict] = []

        # Ensure output directory and backup directory exist
        self._path.parent.mkdir(parents=True, exist_ok=True)
        Path(config.BACKUP_DIR).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_player(self, player: str) -> None:
        self._current_player = player

    def write_entry(self, player: str, metrics: BaseMetrics) -> int:
        """
        Append `metrics` to the entry list and atomically flush to disk.

        Returns the assigned entry_id (1-based).
        """
        self._current_player = player
        entry_id = len(self._entries) + 1
        entry = self._build_entry(entry_id, player, metrics)
        self._entries.append(entry)

        payload = {
            "session_id": self._session_id,
            "mode": self._mode,
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "current_player": self._current_player,
            "entries": self._entries,
        }

        self._atomic_write(payload)
        return entry_id

    def write_invalid_entry(self, player: str) -> int:
        """Record an entry where Rapsodo detected no ball (displays ---)."""
        self._current_player = player
        entry_id = len(self._entries) + 1
        entry = {
            "entry_id": entry_id,
            "player": player,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        entry["velocity_mph"] = None
        entry["total_spin_rpm"] = None
        entry["spin_direction"] = None
        entry["spin_efficiency_pct"] = None
        entry["note"] = "invalid — no data detected"
        self._entries.append(entry)

        payload = {
            "session_id": self._session_id,
            "mode": self._mode,
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "current_player": self._current_player,
            "entries": self._entries,
        }

        self._atomic_write(payload)
        return entry_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_entry(
        entry_id: int, player: str, metrics: BaseMetrics
    ) -> dict:
        base = {
            "entry_id": entry_id,
            "player": player,
            "timestamp": metrics.timestamp.isoformat(timespec="seconds"),
        }

        if isinstance(metrics, PitchMetrics):
            base["velocity_mph"] = metrics.velocity_mph
            base["total_spin_rpm"] = metrics.total_spin_rpm
            base["spin_direction"] = metrics.spin_direction
            base["spin_efficiency_pct"] = metrics.spin_efficiency_pct
        else:
            # Fallback: dump all non-timestamp fields
            base.update(metrics.field_values())

        return base

    def _atomic_write(self, payload: dict) -> None:
        temp_path = self._path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self._path)
        except OSError as exc:
            logger.error("JSON write failed: %s. Will retry on next entry.", exc)
            # Clean up temp file if it exists
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

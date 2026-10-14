from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import Src.rapsodo.RapsodoMetricsOCRV2.config as config
from Src.rapsodo.RapsodoMetricsOCRV2.models import BaseMetrics
from Src.rapsodo.RapsodoMetricsOCRV2.modes.pitching import PitchMetrics

logger = logging.getLogger(__name__)


class DataWriter:
    """
    Persist metrics to a JSON file using atomic writes (temp-file + os.replace).

    The JSON schema includes a top-level session header and a growing list of
    entry dicts.  Each entry's field names are mode-specific (see SDD §3.7).
    """

    def __init__(
        self,
        output_path: str,
        mode: str,
        session_id: str,
        velocity_source_unit: str = "mph",
        velocity_output_unit: str | None = None,
        break_source_unit: str = "in",
        break_output_unit: str | None = None,
    ) -> None:
        """
        Fix 34 — 雙單位層（source + output）：
          source  = Rapsodo iPad UI 實際顯示單位、OCR 讀到的原始數字單位
          output  = JSON 要寫出的單位；若省略則預設 = source（不做換算）
        兩者相同時走「只改 JSON key 尾綴」的舊路徑（byte-identical）。
        兩者不同時在 write 前用 config 的換算常數乘一次。
        """
        self._path = Path(output_path)
        self._mode = mode
        self._session_id = session_id
        if velocity_source_unit not in ("mph", "kph"):
            raise ValueError(
                f"velocity_source_unit must be 'mph' or 'kph', got {velocity_source_unit!r}"
            )
        if break_source_unit not in ("in", "cm"):
            raise ValueError(
                f"break_source_unit must be 'in' or 'cm', got {break_source_unit!r}"
            )
        velocity_output_unit = velocity_output_unit or velocity_source_unit
        break_output_unit = break_output_unit or break_source_unit
        if velocity_output_unit not in ("mph", "kph"):
            raise ValueError(
                f"velocity_output_unit must be 'mph' or 'kph', got {velocity_output_unit!r}"
            )
        if break_output_unit not in ("in", "cm"):
            raise ValueError(
                f"break_output_unit must be 'in' or 'cm', got {break_output_unit!r}"
            )
        self._velocity_source_unit = velocity_source_unit
        self._velocity_output_unit = velocity_output_unit
        self._break_source_unit = break_source_unit
        self._break_output_unit = break_output_unit
        self._current_player: str = ""
        self._entries: list[dict] = []

        # Ensure output directory and backup directory exist
        self._path.parent.mkdir(parents=True, exist_ok=True)
        Path(config.BACKUP_DIR).mkdir(parents=True, exist_ok=True)

    @property
    def _velocity_key(self) -> str:
        """JSON key for pitching velocity, suffix follows OUTPUT unit."""
        return (
            "velocity_kph(球速)" if self._velocity_output_unit == "kph"
            else "velocity_mph(球速)"
        )

    @property
    def _v_break_key(self) -> str:
        """JSON key for vertical break, suffix follows OUTPUT unit."""
        return (
            "v_break_cm(垂直位移)" if self._break_output_unit == "cm"
            else "v_break_in(垂直位移)"
        )

    @property
    def _h_break_key(self) -> str:
        """JSON key for horizontal break, suffix follows OUTPUT unit."""
        return (
            "h_break_cm(水平位移)" if self._break_output_unit == "cm"
            else "h_break_in(水平位移)"
        )

    def _convert_velocity(self, value: float | None) -> float | None:
        """Fix 34 — convert source→output velocity, rounding to 1 decimal."""
        if value is None or self._velocity_source_unit == self._velocity_output_unit:
            return value
        if self._velocity_source_unit == "mph" and self._velocity_output_unit == "kph":
            return round(value * config.KPH_PER_MPH, 1)
        if self._velocity_source_unit == "kph" and self._velocity_output_unit == "mph":
            return round(value * config.MPH_PER_KPH, 1)
        return value  # unreachable — all 4 combos covered above

    def _convert_break(self, value: float | None) -> float | None:
        """Fix 34 — convert source→output V/H break, rounding to 1 decimal."""
        if value is None or self._break_source_unit == self._break_output_unit:
            return value
        if self._break_source_unit == "in" and self._break_output_unit == "cm":
            return round(value * config.CM_PER_INCH, 1)
        if self._break_source_unit == "cm" and self._break_output_unit == "in":
            return round(value * config.INCH_PER_CM, 1)
        return value

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
        if self._mode == "pitching":
            entry[self._velocity_key] = None
            entry["total_spin_rpm(旋轉數)"] = None
            entry["spin_direction(旋轉方向)"] = None
            entry["spin_efficiency_pct(旋轉效率)"] = None
            entry[self._v_break_key] = None
            entry[self._h_break_key] = None
        else:
            entry["exit_velocity_mph(出棒速度)"] = None
            entry["launch_angle_deg(擊球角度)"] = None
            entry["distance_ft(飛行距離)"] = None
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

    def _build_entry(
        self, entry_id: int, player: str, metrics: BaseMetrics
    ) -> dict:
        base = {
            "entry_id": entry_id,
            "player": player,
            "timestamp": metrics.timestamp.isoformat(timespec="seconds"),
        }
        if isinstance(metrics, PitchMetrics):
            # Fix 34 — dataclass 欄位存 source 單位原始數字，寫入前依 output
            # 單位換算（source==output 時 _convert_* 無操作，byte-identical）。
            base[self._velocity_key] = self._convert_velocity(metrics.velocity_mph)
            base["total_spin_rpm(旋轉數)"] = metrics.total_spin_rpm
            base["spin_direction(旋轉方向)"] = metrics.spin_direction
            base["spin_efficiency_pct(旋轉效率)"] = metrics.spin_efficiency_pct
            base[self._v_break_key] = self._convert_break(metrics.v_break_in)
            base[self._h_break_key] = self._convert_break(metrics.h_break_in)
        else:
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

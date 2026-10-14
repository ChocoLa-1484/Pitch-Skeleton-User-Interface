from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import Src.rapsodo.RapsodoMetricsOCRV2.config as config

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manage player identity and session lifecycle.

    On end_session(), the live JSON file is copied to backup/ with a
    name that encodes mode and session_id.
    """

    def __init__(self) -> None:
        self._session_id: str = ""
        self._mode: str = ""
        self._current_player: str = ""
        self._json_path: str = ""  # set by start_session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, mode: str, first_player: str,
                       json_path: str = "") -> str:
        """
        Initialise a new session.  Returns the session_id string.

        *json_path* is the actual output JSON path used by the DataWriter.
        If provided, ``end_session()`` will back up that file instead of
        the generic ``config.OUTPUT_JSON_PATH``.
        """
        self._mode = mode
        self._current_player = first_player
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._json_path = json_path
        logger.info(
            "Session started: id=%s  mode=%s  player=%s",
            self._session_id, self._mode, self._current_player,
        )
        return self._session_id

    def change_player(self, new_player: str) -> None:
        logger.info(
            "Player changed: %s → %s", self._current_player, new_player
        )
        self._current_player = new_player

    def end_session(self) -> None:
        """
        End the session and back up the live JSON file.
        """
        logger.info("Session %s ended.", self._session_id)
        self._backup_json()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_player(self) -> str:
        return self._current_player

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _backup_json(self) -> None:
        source = Path(self._json_path or config.OUTPUT_JSON_PATH)
        if not source.exists():
            logger.warning("No JSON file to back up at %s.", source)
            return

        backup_dir = Path(config.BACKUP_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)

        backup_name = (
            f"rapsodo_data_{self._mode}_{self._session_id}.json"
        )
        dest = backup_dir / backup_name

        try:
            shutil.copy2(source, dest)
            logger.info("Session backed up to %s.", dest)
        except OSError as exc:
            logger.error("Backup failed: %s", exc)

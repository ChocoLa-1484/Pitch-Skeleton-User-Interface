from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RelativeROI:
    """Region of interest defined as fractions of the screen dimensions."""
    x: float  # left edge as fraction of screen width
    y: float  # top edge as fraction of screen height
    w: float  # width as fraction of screen width
    h: float  # height as fraction of screen height

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RelativeROI):
            return NotImplemented
        return self.x == other.x and self.y == other.y and self.w == other.w and self.h == other.h

    def __hash__(self) -> int:
        return hash((self.x, self.y, self.w, self.h))


@dataclass
class BaseMetrics:
    """Base class for all mode-specific metric dataclasses."""
    timestamp: datetime = field(default_factory=datetime.now)

    def field_values(self) -> dict:
        """Return non-timestamp fields as a dict. Subclasses inherit this."""
        return {
            k: v
            for k, v in self.__dict__.items()
            if k != "timestamp"
        }


@dataclass
class SessionData:
    """Metadata for a single recording session."""
    session_id: str
    mode: str
    current_player: str
    started_at: datetime = field(default_factory=datetime.now)

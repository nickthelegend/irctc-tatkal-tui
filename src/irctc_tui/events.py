"""Event types passed from the automation engine to any front-end (the TUI).

Keeping these in their own module avoids a circular import between
``automation`` and ``app`` and lets both agree on the vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    """Coarse state of the booking run — drives the status line in the TUI."""

    IDLE = "Idle"
    PREFLIGHT = "Pre-flight check"
    LAUNCHING = "Launching browser"
    LOGIN = "Waiting for login"
    WAITING_START = "Waiting for start time"
    SEARCHING = "Searching trains"
    POLLING = "Polling availability"
    AVAILABLE = "Seat available"
    BOOKING = "Booking"
    PASSENGERS = "Entering passengers"
    HANDOFF = "Handed off to you"
    DONE = "Done"
    STOPPED = "Stopped"
    ERROR = "Error"


class Level(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARN = "warn"
    ERROR = "error"
    HUMAN = "human"  # something the user must do in the browser now


@dataclass
class BotEvent:
    """A single message from the engine.

    ``kind`` is one of: ``log``, ``phase``, ``countdown``, ``status``, ``done``.
    The TUI switches on it to decide what to update.
    """

    kind: str
    message: str = ""
    level: Level = Level.INFO
    phase: Phase | None = None
    data: dict[str, Any] = field(default_factory=dict)

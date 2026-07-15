"""Parse the IRCTC search-results page into structured trains + availability.

IRCTC renders each train as an ``<app-train-avl-enq>`` card with a heading like
``NARAYANADRI EXPRESS (12734)`` and a row of per-class cells showing the class
code, fare, and availability (``AVAILABLE-0021``, ``RAC 5``, ``WL 12``, …).

The parser is deliberately **text-based**: instead of depending on fragile
sub-selectors for every field, it reads each card's / cell's text and pulls out
the pieces with regexes. That survives IRCTC's frequent DOM reshuffles far better
than pinning exact element paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import selectors as S
from .selectors import Availability, classify_availability

# Class codes IRCTC uses, longest-first so e.g. "3E" wins before a bare "3".
_CLASS_CODES = ["1A", "2A", "3A", "3E", "EC", "CC", "2S", "SL", "FC"]

_NAME_NUM_RE = re.compile(r"(.+?)\s*\((\d{4,5})\)")
_TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")
_FARE_RE = re.compile(r"(?:₹|RS\.?|INR)\s?([\d,]+)", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"(CURR[_ ]?AVBL|AVAILABLE|AVBL|RAC|GNWL|RLWL|PQWL|RSWL|WL|REGRET"
    r"|NOT\s*AVAILABLE|TRAIN\s*DEPARTED|CHART\s*PREPARED|CANCELLED)[-\s]*(\d+)?",
    re.IGNORECASE,
)


@dataclass
class ClassAvailability:
    """Availability of one class on one train (for the searched date)."""

    class_code: str
    status_raw: str
    availability: Availability
    fare: str = ""

    @property
    def bookable(self) -> bool:
        return self.availability.bookable

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_code": self.class_code,
            "status_raw": self.status_raw,
            "availability": self.availability.value,
            "fare": self.fare,
            "bookable": self.bookable,
        }


@dataclass
class Train:
    """One train in the results, with its parsed class availabilities."""

    number: str
    name: str
    departure: str = ""
    arrival: str = ""
    duration: str = ""
    classes: list[ClassAvailability] = field(default_factory=list)

    def availability_for(self, class_code: str) -> ClassAvailability | None:
        code = (class_code or "").strip().upper()
        return next((c for c in self.classes if c.class_code == code), None)

    def best_bookable(self) -> ClassAvailability | None:
        return next((c for c in self.classes if c.bookable), None)

    def label(self) -> str:
        return f"{self.name} ({self.number})" if self.number else self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "departure": self.departure,
            "arrival": self.arrival,
            "duration": self.duration,
            "classes": [c.to_dict() for c in self.classes],
        }


# --------------------------------------------------------------------------- #
# Pure parsing helpers (unit-testable without a browser)
# --------------------------------------------------------------------------- #


def parse_train_header(text: str) -> Train:
    """Pull name/number/times out of a train card's text."""
    flat = " ".join((text or "").split())
    m = _NAME_NUM_RE.search(flat)
    if m:
        name, number = m.group(1).strip(" -·|"), m.group(2)
    else:
        name, number = (flat[:50] if flat else ""), ""
    times = _TIME_RE.findall(text or "")
    departure = times[0] if len(times) >= 1 else ""
    arrival = times[1] if len(times) >= 2 else ""
    return Train(number=number, name=name, departure=departure, arrival=arrival)


def parse_class_cell(text: str) -> ClassAvailability | None:
    """Parse one class cell's text into a :class:`ClassAvailability` (or None)."""
    flat = " ".join((text or "").split())
    if not flat:
        return None
    code = next((c for c in _CLASS_CODES if re.search(rf"(?<![A-Z0-9]){c}(?![A-Z0-9])", flat)), "")
    if not code:
        return None
    status_match = _STATUS_RE.search(flat)
    status_raw = " ".join(status_match.group(0).split()) if status_match else ""
    fare_match = _FARE_RE.search(flat)
    fare = f"₹{fare_match.group(1)}" if fare_match else ""
    return ClassAvailability(code, status_raw, classify_availability(status_raw), fare)


def _css(candidates: list[str]) -> str:
    css = [c for c in candidates if not c.startswith(("text=", "xpath=", "//"))]
    return ", ".join(css) if css else candidates[0]


# --------------------------------------------------------------------------- #
# DOM parsing (async, against a Playwright page)
# --------------------------------------------------------------------------- #


async def parse_results(page, max_trains: int = 40) -> list[Train]:
    """Return the list of :class:`Train` parsed from the results ``page``."""
    cards = page.locator(_css(S.TRAIN_CARD))
    try:
        count = min(await cards.count(), max_trains)
    except Exception:  # noqa: BLE001
        return []

    trains: list[Train] = []
    for i in range(count):
        card = cards.nth(i)
        try:
            full = await card.inner_text()
        except Exception:  # noqa: BLE001
            continue
        train = parse_train_header(full)

        cells = card.locator(_css(S.RESULT_CLASS_CELL))
        seen: set[str] = set()
        try:
            n_cells = await cells.count()
        except Exception:  # noqa: BLE001
            n_cells = 0
        for j in range(n_cells):
            try:
                cell_text = await cells.nth(j).inner_text()
            except Exception:  # noqa: BLE001
                continue
            parsed = parse_class_cell(cell_text)
            if parsed and parsed.class_code not in seen:
                seen.add(parsed.class_code)
                train.classes.append(parsed)

        if train.number or train.classes:
            trains.append(train)
    return trains


def pick_target(trains: list[Train], class_code: str, train_number: str = "") -> tuple[Train, ClassAvailability] | None:
    """Choose the best (train, class) to book.

    Priority: a bookable target-class on the requested train number, else the
    first train with a bookable target-class, else the requested train's
    target-class (even if not bookable, for reporting), else the first train's.
    """
    code = (class_code or "").strip().upper()
    want_num = (train_number or "").strip()

    if want_num:
        target = next((t for t in trains if want_num in t.number), None)
        if target:
            ca = target.availability_for(code)
            return (target, ca) if ca else None

    for t in trains:  # first bookable target-class across all trains
        ca = t.availability_for(code)
        if ca and ca.bookable:
            return t, ca
    for t in trains:  # else first train that even has the class (for reporting)
        ca = t.availability_for(code)
        if ca:
            return t, ca
    return None

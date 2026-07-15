"""Parse the IRCTC search-results page into structured trains + availability.

Verified against the **real** IRCTC DOM (SC→Tirupati, 24-Jul-2026). Each train is
an ``<app-train-avl-enq>`` card with:

* ``.train-heading strong`` → ``"KRISHNA EXPRESS (17406)"``
* ``.time`` nodes → departure / arrival
* a ``p-tabmenu`` of class tabs; the clicked one has ``.ui-state-active`` and
  its ``.hidden-xs`` reads e.g. ``"Sleeper (SL)"``
* date-wise availability cells (``td.link .pre-avl``), each a date ``<strong>``
  plus a status div whose **class** is ``WL`` / ``RAC`` / ``AVAILABLE`` /
  ``REGRET`` and whose text is e.g. ``"WL30"``, ``"RAC 33"``, ``"REGRET"``.

Availability only appears once a class tab is clicked, so the engine clicks the
target class first; this parser then reads the loaded (active) class' cells and
matches the journey-date cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import selectors as S
from .selectors import Availability, classify_availability

_CLASS_CODES = ["1A", "2A", "3A", "3E", "EC", "CC", "2S", "SL", "FC"]
_MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_NAME_NUM_RE = re.compile(r"([A-Za-z][A-Za-z .&/-]*?)\s*\((\d{4,5})\)")
_TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")
_FARE_RE = re.compile(r"(?:₹|RS\.?|INR)\s?([\d,]+)", re.IGNORECASE)
_CODE_IN_PARENS_RE = re.compile(r"\((1A|2A|3A|3E|SL|CC|EC|2S|FC)\)")
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
    """Pull name/number (and best-effort times) from a train card's text."""
    flat = " ".join((text or "").split())
    m = _NAME_NUM_RE.search(flat)
    if m:
        name, number = m.group(1).strip(" -·|"), m.group(2)
    else:
        name, number = (flat[:50] if flat else ""), ""
    times = _TIME_RE.findall(text or "")
    departure = times[0] if len(times) >= 1 else ""
    # In IRCTC card text the order is dep, duration, arr — so arrival is the last.
    arrival = times[-1] if len(times) >= 2 else ""
    return Train(number=number, name=name, departure=departure, arrival=arrival)


def code_from_label(label: str) -> str:
    """"Sleeper (SL)" → "SL"; "AC 3 Tier (3A)" → "3A"."""
    m = _CODE_IN_PARENS_RE.search(label or "")
    if m:
        return m.group(1)
    up = (label or "").strip().upper()
    return next((c for c in _CLASS_CODES if re.search(rf"(?<![A-Z0-9]){c}(?![A-Z0-9])", up)), "")


def date_token(journey_date: str) -> str:
    """"24-07-2026" → "24 Jul" (matches the date shown in an availability cell)."""
    parts = (journey_date or "").split("-")
    if len(parts) != 3:
        return ""
    day, month, _year = parts
    try:
        return f"{day} {_MONTHS_SHORT[int(month) - 1]}"
    except (ValueError, IndexError):
        return ""


def extract_status(text: str) -> str:
    """"Fri, 24 Jul WL30" → "WL30"; "Fri, 24 Jul REGRET" → "REGRET"; else ""."""
    m = _STATUS_RE.search(text or "")
    return " ".join(m.group(0).split()) if m else ""


def extract_fare(text: str) -> str:
    m = _FARE_RE.search(text or "")
    return f"₹{m.group(1)}" if m else ""


def status_for_date(cell_texts: list[str], journey_date: str = "") -> str:
    """Pick the availability status for the journey date from date cells.

    Cells look like ``["Fri, 24 Jul WL30", "Sat, 25 Jul WL31", …]``. Prefer the
    cell whose date matches ``journey_date``; otherwise the first cell that has a
    status.
    """
    token = date_token(journey_date)
    fallback = ""
    for text in cell_texts:
        status = extract_status(text)
        if not status:
            continue
        if token and token in " ".join(text.split()):
            return status
        if not fallback:
            fallback = status
    return fallback


def _css(candidates: list[str]) -> str:
    css = [c for c in candidates if not c.startswith(("text=", "xpath=", "//"))]
    return ", ".join(css) if css else candidates[0]


# --------------------------------------------------------------------------- #
# DOM parsing (async, against a Playwright page)
# --------------------------------------------------------------------------- #


async def _texts(card, candidates: list[str]) -> list[str]:
    for sel in [_css([c]) for c in candidates]:
        try:
            loc = card.locator(sel)
            if await loc.count():
                return [t.strip() for t in await loc.all_inner_texts() if t.strip()]
        except Exception:  # noqa: BLE001
            continue
    return []


async def _first_text(card, candidates: list[str]) -> str:
    texts = await _texts(card, candidates)
    return texts[0] if texts else ""


async def parse_results(page, journey_date: str = "", want_class: str = "",
                        max_trains: int = 40) -> list[Train]:
    """Parse every ``<app-train-avl-enq>`` card into a :class:`Train`."""
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

        heading = await _first_text(card, S.TRAIN_HEADING)
        train = parse_train_header(heading or full)

        # Precise departure/arrival from the .time nodes when available.
        time_texts = [re.sub(r"[^\d:]", "", t) for t in await _texts(card, S.TRAIN_TIME)]
        time_texts = [t for t in time_texts if _TIME_RE.fullmatch(t)]
        if time_texts:
            train.departure = time_texts[0]
            train.arrival = time_texts[-1]

        # The active (clicked) class and every offered class.
        active_code = code_from_label(await _first_text(card, S.ACTIVE_CLASS_TAB)) or (want_class or "")
        offered = [code_from_label(t) for t in await _texts(card, S.ALL_CLASS_TABS)]
        offered = [c for c in offered if c]

        # Availability of the active class for the journey date.
        cell_texts = await _texts(card, S.AVAIL_DATE_CELL)
        status_raw = status_for_date(cell_texts, journey_date)
        fare = extract_fare(full)

        if active_code and status_raw:
            train.classes.append(
                ClassAvailability(active_code, status_raw, classify_availability(status_raw), fare)
            )
        for code in offered:
            if not train.availability_for(code):
                train.classes.append(ClassAvailability(code, "", Availability.UNKNOWN))

        if train.number or train.classes:
            trains.append(train)
    return trains


def pick_target(trains: list[Train], class_code: str,
                train_number: str = "") -> tuple[Train, ClassAvailability] | None:
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

    for t in trains:
        ca = t.availability_for(code)
        if ca and ca.bookable:
            return t, ca
    for t in trains:
        ca = t.availability_for(code)
        if ca:
            return t, ca
    return None

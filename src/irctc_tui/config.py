"""Configuration model for the IRCTC Tatkal TUI.

Everything the user can tweak lives here as plain dataclasses that round-trip to
JSON. The config file is git-ignored (see ``.gitignore``) because it may hold an
IRCTC username/password.

Design notes
------------
* No third-party validation library — stdlib ``dataclasses`` + ``json`` keeps the
  dependency surface tiny and the file easy to hand-edit.
* ``from_dict`` is deliberately lenient: unknown keys are ignored and missing keys
  fall back to defaults, so a config written by an older version still loads.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Enumerable choices — used both for validation and to populate TUI dropdowns.
# --------------------------------------------------------------------------- #

QUOTAS: list[str] = [
    "TATKAL",
    "PREMIUM TATKAL",
    "GENERAL",
    "LADIES",
    "LOWER BERTH/SR.CITIZEN",
    "PHYSICALLY HANDICAPPED",
    "DUTY PASS",
]

# Class codes as IRCTC labels them. The value is what shows in the dropdown /
# on the train card; the key before the dash is the code the site uses.
TRAVEL_CLASSES: list[str] = [
    "SL",   # Sleeper
    "3A",   # AC 3 Tier
    "3E",   # AC 3 Tier (Economy)
    "2A",   # AC 2 Tier
    "1A",   # AC First Class
    "CC",   # AC Chair Car
    "EC",   # Exec. Chair Car
    "2S",   # Second Sitting
    "FC",   # First Class
]

GENDERS: list[str] = ["Male", "Female", "Transgender"]

BERTH_PREFERENCES: list[str] = [
    "No Preference",
    "Lower",
    "Middle",
    "Upper",
    "Side Lower",
    "Side Upper",
    "Window Side",
    "Cabin",
    "Coupe",
]

FOOD_PREFERENCES: list[str] = [
    "No Food",
    "Veg",
    "Non Veg",
    "Jain",
    "Veg (Diet)",
]

NATIONALITIES: list[str] = ["India", "Other"]

BROWSERS: list[str] = ["chromium", "firefox", "webkit"]


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class Passenger:
    """A single traveller on the ticket."""

    name: str = ""
    age: int = 0
    gender: str = "Male"
    berth_preference: str = "No Preference"
    food_preference: str = "No Food"
    nationality: str = "India"
    senior_citizen: bool = False
    # IRCTC lets you opt a passenger out of auto-upgrade; exposed for completeness.
    opt_berth_only_if_confirmed: bool = False

    def summary(self) -> str:
        bits = [self.name or "(unnamed)", f"{self.age}y", self.gender[:1]]
        if self.berth_preference != "No Preference":
            bits.append(self.berth_preference)
        return " · ".join(bits)


@dataclass
class AccountConfig:
    """IRCTC login details.

    ``password`` is optional and stored in plaintext in the (git-ignored) config
    when set. Leaving it blank is safer — you then type it in the browser at run
    time. The CAPTCHA is *always* solved by you regardless.
    """

    username: str = ""
    password: str = ""
    # When True the tool opens the login modal and pre-fills what it can, then
    # waits for you to solve the CAPTCHA and press SIGN IN. When False it does
    # not touch login at all — you log in entirely by hand.
    auto_login: bool = True
    # Reuse a saved browser session (cookies) so you can skip login on re-runs.
    reuse_session: bool = False


@dataclass
class JourneyConfig:
    """The trip itself."""

    # What to type into the station autocomplete. A station code (e.g. "SC",
    # "HYB", "TPTY") is the most reliable — the top suggestion is then selected.
    from_station: str = "SC"          # Secunderabad Jn (most Hyderabad→Tirupati trains)
    to_station: str = "TPTY"          # Tirupati
    # DD-MM-YYYY. Defaults to the trip in the original request.
    journey_date: str = "24-07-2026"
    travel_class: str = "SL"
    quota: str = "TATKAL"
    # Optionally lock onto one train (by number, e.g. "12734"). Empty = book the
    # first train in the results that has the target class available.
    train_number: str = ""
    # Optional boarding point override (station code). Empty = use origin.
    boarding_station: str = ""


@dataclass
class TimingConfig:
    """When and how often the tool acts."""

    # Poll availability every N seconds while waiting for a seat to open.
    check_interval_seconds: float = 15.0
    # Random extra delay (0..N s) added to each interval so requests aren't
    # perfectly periodic. Keep this > 0 to be a polite client.
    jitter_seconds: float = 3.0
    # "HH:MM:SS" local time to hold the first search until (Tatkal AC opens
    # 10:00, non-AC 11:00 one day before travel). Empty = start immediately.
    start_time: str = ""
    # Stop after this many search attempts. 0 = unlimited (until you quit).
    max_attempts: int = 0
    # Retry the search loop if a single attempt throws (network hiccups etc.).
    retry_on_error: bool = True


@dataclass
class BehaviorConfig:
    """How the browser and booking flow behave."""

    # When a seat is available, proceed straight into passenger entry.
    auto_book_when_available: bool = True
    # Hard stop before the payment page. Locked on: the tool never pays.
    stop_before_payment: bool = True
    # Shown to you on the payment screen for convenience — never auto-submitted.
    upi_id: str = ""
    contact_mobile: str = ""
    save_screenshots: bool = True
    # Ring a looping alarm when a seat is found / payment hand-off is reached, so
    # you can step away and be called back. You silence it manually in the TUI.
    alarm_on_success: bool = True
    # Path to a .wav/.mp3 to ring. Empty = a built-in tune synthesised on first use.
    alarm_sound_path: str = ""
    # Headed is the default (you asked to watch it). Headless is offered for
    # dry-runs but IRCTC actively blocks headless traffic.
    headed: bool = True
    slow_mo_ms: int = 0
    browser: str = "chromium"


@dataclass
class AppConfig:
    """Top-level config aggregating every section."""

    account: AccountConfig = field(default_factory=AccountConfig)
    journey: JourneyConfig = field(default_factory=JourneyConfig)
    passengers: list[Passenger] = field(default_factory=list)
    timing: TimingConfig = field(default_factory=TimingConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)

    # ---- serialisation ---------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        def build(dc_type, raw):
            if not isinstance(raw, dict):
                return dc_type()
            known = {f.name for f in fields(dc_type)}
            return dc_type(**{k: v for k, v in raw.items() if k in known})

        passengers = [build(Passenger, p) for p in data.get("passengers", [])]
        return cls(
            account=build(AccountConfig, data.get("account", {})),
            journey=build(JourneyConfig, data.get("journey", {})),
            passengers=passengers,
            timing=build(TimingConfig, data.get("timing", {})),
            behavior=build(BehaviorConfig, data.get("behavior", {})),
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> AppConfig:
        path = Path(path)
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # ---- validation ------------------------------------------------------- #

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means good to go."""
        problems: list[str] = []
        j = self.journey
        if not j.from_station.strip():
            problems.append("Journey: 'from' station is empty.")
        if not j.to_station.strip():
            problems.append("Journey: 'to' station is empty.")
        if j.from_station.strip().upper() == j.to_station.strip().upper():
            problems.append("Journey: 'from' and 'to' stations are the same.")
        if not _looks_like_ddmmyyyy(j.journey_date):
            problems.append("Journey: date must be DD-MM-YYYY (e.g. 24-07-2026).")
        if j.quota not in QUOTAS:
            problems.append(f"Journey: unknown quota '{j.quota}'.")
        if j.travel_class not in TRAVEL_CLASSES:
            problems.append(f"Journey: unknown class '{j.travel_class}'.")
        if not self.passengers:
            problems.append("Passengers: add at least one passenger.")
        for i, p in enumerate(self.passengers, 1):
            if not p.name.strip():
                problems.append(f"Passenger {i}: name is empty.")
            if not (1 <= int(p.age or 0) <= 125):
                problems.append(f"Passenger {i}: age must be 1–125.")
        if self.timing.check_interval_seconds < 3:
            problems.append("Timing: interval below 3s is abusive — raise it.")
        if self.timing.start_time and not _looks_like_hhmmss(self.timing.start_time):
            problems.append("Timing: start time must be HH:MM:SS (e.g. 10:00:00).")
        if self.account.auto_login and not self.account.username.strip():
            problems.append("Account: auto-login is on but username is empty.")
        return problems


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _looks_like_ddmmyyyy(value: str) -> bool:
    parts = value.split("-")
    if len(parts) != 3:
        return False
    d, m, y = parts
    return d.isdigit() and m.isdigit() and y.isdigit() and len(d) == 2 and len(m) == 2 and len(y) == 4


def _looks_like_hhmmss(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 3:
        return False
    return all(p.isdigit() for p in parts)


def default_config_path() -> Path:
    """Where the config lives by default: ``./config.json`` in the CWD."""
    return Path.cwd() / "config.json"


def example_config() -> AppConfig:
    """A fully populated example: Hyderabad (Secunderabad) → Tirupati, Tatkal."""
    return AppConfig(
        account=AccountConfig(username="your_irctc_username", password="", auto_login=True),
        journey=JourneyConfig(
            from_station="SC",
            to_station="TPTY",
            journey_date="24-07-2026",
            travel_class="SL",
            quota="TATKAL",
            train_number="",
        ),
        passengers=[
            Passenger(name="Passenger One", age=28, gender="Male", berth_preference="Lower"),
        ],
        timing=TimingConfig(check_interval_seconds=15.0, jitter_seconds=3.0, start_time="11:00:00"),
        behavior=BehaviorConfig(contact_mobile="", upi_id=""),
    )

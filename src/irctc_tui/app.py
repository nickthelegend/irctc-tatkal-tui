"""Textual TUI for the IRCTC Tatkal booker.

A single tabbed screen where **every** booking parameter is editable:

* **Journey** — stations, date, class, quota, target train
* **Passengers** — add/remove an unlimited passenger list
* **Account** — IRCTC username/password, auto-login, session reuse
* **Timing** — poll interval, jitter, scheduled start, retry/limits
* **Browser** — auto-book, headed/headless, browser engine, screenshots
* **Run** — live status, start/stop, and a colour-coded event log

The Run tab starts the :class:`~irctc_tui.automation.IRCTCBot` as a Textual
worker on the same event loop, so browser automation and UI updates cooperate
without threads.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)
from textual.worker import Worker

from . import config as C
from .automation import IRCTCBot
from .config import AppConfig, Passenger
from .events import BotEvent, Level, Phase

LEVEL_MARKUP = {
    Level.INFO: "white",
    Level.SUCCESS: "bold green",
    Level.WARN: "yellow",
    Level.ERROR: "bold red",
    Level.HUMAN: "bold magenta",
}


def _opts(values: list[str]) -> list[tuple[str, str]]:
    """Turn a list of strings into Select (label, value) option tuples."""
    return [(v, v) for v in values]


class IRCTCApp(App):
    """The application. One instance == one config being edited/run."""

    CSS_PATH = "app.tcss"
    TITLE = "IRCTC Tatkal TUI"
    SUB_TITLE = "fill forms · poll availability · you solve CAPTCHA & pay"

    BINDINGS = [
        ("ctrl+s", "save", "Save"),
        ("ctrl+o", "load", "Load"),
        ("ctrl+r", "start", "Start"),
        ("ctrl+t", "stop", "Stop"),
        ("f5", "validate", "Validate"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, config_path: str | Path | None = None) -> None:
        super().__init__()
        self.config_path = Path(config_path) if config_path else C.default_config_path()
        self.cfg: AppConfig = AppConfig.load(self.config_path)
        if not self.cfg.passengers:
            self.cfg = self.cfg if self.config_path.exists() else C.example_config()
        self._passengers: list[Passenger] = list(self.cfg.passengers)
        self._bot: IRCTCBot | None = None
        self._worker: Worker | None = None

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-journey"):
            with TabPane("Journey", id="tab-journey"):
                yield from self._journey_tab()
            with TabPane("Passengers", id="tab-passengers"):
                yield from self._passengers_tab()
            with TabPane("Account", id="tab-account"):
                yield from self._account_tab()
            with TabPane("Timing", id="tab-timing"):
                yield from self._timing_tab()
            with TabPane("Browser", id="tab-browser"):
                yield from self._browser_tab()
            with TabPane("Run", id="tab-run"):
                yield from self._run_tab()
        yield Footer()

    # -- tab builders ---------------------------------------------------- #

    def _journey_tab(self) -> ComposeResult:
        j = self.cfg.journey
        yield Static("Where and when", classes="section-title")
        yield Static(
            "Type a station code (SC, HYB, KCG, TPTY…) — the top autocomplete match is used.",
            classes="hint",
        )
        with Grid(classes="form-grid"):
            yield Label("From station", classes="field-label")
            yield Input(value=j.from_station, id="from_station", placeholder="e.g. SC")
            yield Label("To station", classes="field-label")
            yield Input(value=j.to_station, id="to_station", placeholder="e.g. TPTY")
            yield Label("Journey date", classes="field-label")
            yield Input(value=j.journey_date, id="journey_date", placeholder="DD-MM-YYYY")
            yield Label("Class", classes="field-label")
            yield Select(_opts(C.TRAVEL_CLASSES), value=j.travel_class, id="travel_class", allow_blank=False)
            yield Label("Quota", classes="field-label")
            yield Select(_opts(C.QUOTAS), value=j.quota, id="quota", allow_blank=False)
            yield Label("Train no. (optional)", classes="field-label")
            yield Input(value=j.train_number, id="train_number", placeholder="e.g. 12734 — blank = first match")

    def _passengers_tab(self) -> ComposeResult:
        yield Static("Passengers", classes="section-title")
        yield Static("Add each traveller, then use the Run tab to book.", classes="hint")
        with Grid(id="passenger-form"):
            yield Input(placeholder="Full name", id="p_name")
            yield Input(placeholder="Age", id="p_age", type="integer")
            yield Select(_opts(C.GENDERS), value="Male", id="p_gender", allow_blank=False)
            yield Select(_opts(C.BERTH_PREFERENCES), value="No Preference", id="p_berth", allow_blank=False)
            yield Select(_opts(C.FOOD_PREFERENCES), value="No Food", id="p_food", allow_blank=False)
        with Horizontal(id="passenger-buttons"):
            yield Button("➕ Add passenger", id="add_passenger", variant="success")
            yield Button("🗑 Remove selected", id="remove_passenger", variant="error")
        yield DataTable(id="passenger_table", cursor_type="row")

    def _account_tab(self) -> ComposeResult:
        a = self.cfg.account
        yield Static("IRCTC account", classes="section-title")
        yield Static(
            "Password is optional and stored in plaintext in the git-ignored config. "
            "Leave it blank to type it in the browser. You always solve the CAPTCHA yourself.",
            classes="hint",
        )
        with Grid(classes="form-grid"):
            yield Label("Username", classes="field-label")
            yield Input(value=a.username, id="username", placeholder="IRCTC user id")
            yield Label("Password", classes="field-label")
            yield Input(value=a.password, id="password", password=True, placeholder="(optional)")
            yield Label("Auto-login", classes="field-label")
            yield Switch(value=a.auto_login, id="auto_login")
            yield Label("Reuse session", classes="field-label")
            yield Switch(value=a.reuse_session, id="reuse_session")

    def _timing_tab(self) -> ComposeResult:
        t = self.cfg.timing
        yield Static("Timing & polling", classes="section-title")
        yield Static(
            "Tatkal opens 10:00 (AC) / 11:00 (non-AC) one day before travel. "
            "Set a start time to arm the tool and let it fire on the dot.",
            classes="hint",
        )
        with Grid(classes="form-grid"):
            yield Label("Check interval (s)", classes="field-label")
            yield Input(value=str(t.check_interval_seconds), id="check_interval", type="number")
            yield Label("Jitter (s)", classes="field-label")
            yield Input(value=str(t.jitter_seconds), id="jitter", type="number")
            yield Label("Start at (HH:MM:SS)", classes="field-label")
            yield Input(value=t.start_time, id="start_time", placeholder="blank = start now")
            yield Label("Max attempts", classes="field-label")
            yield Input(value=str(t.max_attempts), id="max_attempts", type="integer", placeholder="0 = unlimited")
            yield Label("Retry on error", classes="field-label")
            yield Switch(value=t.retry_on_error, id="retry_on_error")

    def _browser_tab(self) -> ComposeResult:
        b = self.cfg.behavior
        yield Static("Browser & booking behaviour", classes="section-title")
        with Grid(classes="form-grid"):
            yield Label("Auto-book when free", classes="field-label")
            yield Switch(value=b.auto_book_when_available, id="auto_book")
            yield Label("Headed browser", classes="field-label")
            yield Switch(value=b.headed, id="headed")
            yield Label("Browser engine", classes="field-label")
            yield Select(_opts(C.BROWSERS), value=b.browser, id="browser", allow_blank=False)
            yield Label("Slow-mo (ms)", classes="field-label")
            yield Input(value=str(b.slow_mo_ms), id="slow_mo", type="integer")
            yield Label("Save screenshots", classes="field-label")
            yield Switch(value=b.save_screenshots, id="save_screenshots")
            yield Label("Contact mobile", classes="field-label")
            yield Input(value=b.contact_mobile, id="contact_mobile", placeholder="10-digit mobile")
            yield Label("UPI id (shown only)", classes="field-label")
            yield Input(value=b.upi_id, id="upi_id", placeholder="name@bank — never auto-paid")

    def _run_tab(self) -> ComposeResult:
        with Vertical(id="run-body"):
            with Horizontal(id="status-bar"):
                yield Static("Phase: Idle", id="status_phase", classes="status-item")
                yield Static("Attempts: 0", id="status_attempts", classes="status-item")
                yield Static("Availability: —", id="status_avail", classes="status-item")
                yield Static("Next: —", id="status_countdown", classes="status-item")
            with Horizontal(id="run-buttons"):
                yield Button("▶ Start", id="start", variant="success")
                yield Button("■ Stop", id="stop", variant="error")
                yield Button("💾 Save config", id="save")
                yield Button("✓ Validate", id="validate")
                yield Button("✕ Close browser", id="close_browser")
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        table = self.query_one("#passenger_table", DataTable)
        table.add_columns("Name", "Age", "Gender", "Berth", "Food")
        self._rebuild_passenger_table()
        self._log_line("Welcome. Fill the tabs, then press Start (Ctrl+R).", Level.INFO)
        self._log_line(
            "Reminder: you solve the CAPTCHA, login, and payment in the browser.",
            Level.HUMAN,
        )
        if self.config_path.exists():
            self._log_line(f"Loaded config from {self.config_path}", Level.SUCCESS)

    # ------------------------------------------------------------------ #
    # Passenger management
    # ------------------------------------------------------------------ #

    def _rebuild_passenger_table(self) -> None:
        table = self.query_one("#passenger_table", DataTable)
        table.clear()
        for p in self._passengers:
            table.add_row(p.name or "(unnamed)", str(p.age), p.gender, p.berth_preference, p.food_preference)

    @on(Button.Pressed, "#add_passenger")
    def add_passenger(self) -> None:
        name = self.query_one("#p_name", Input).value.strip()
        age_raw = self.query_one("#p_age", Input).value.strip()
        if not name:
            self.notify("Passenger name is required.", severity="error")
            return
        try:
            age = int(age_raw)
        except ValueError:
            self.notify("Age must be a whole number.", severity="error")
            return
        p = Passenger(
            name=name,
            age=age,
            gender=str(self.query_one("#p_gender", Select).value),
            berth_preference=str(self.query_one("#p_berth", Select).value),
            food_preference=str(self.query_one("#p_food", Select).value),
        )
        self._passengers.append(p)
        self._rebuild_passenger_table()
        self.query_one("#p_name", Input).value = ""
        self.query_one("#p_age", Input).value = ""
        self.notify(f"Added {p.summary()}")

    @on(Button.Pressed, "#remove_passenger")
    def remove_passenger(self) -> None:
        table = self.query_one("#passenger_table", DataTable)
        if not self._passengers:
            return
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._passengers):
            self.notify("Select a passenger row first.", severity="warning")
            return
        removed = self._passengers.pop(row)
        self._rebuild_passenger_table()
        self.notify(f"Removed {removed.name}")

    # ------------------------------------------------------------------ #
    # Config gather / persist
    # ------------------------------------------------------------------ #

    def _collect_config(self) -> AppConfig:
        def f(id_: str) -> str:
            return self.query_one(f"#{id_}", Input).value.strip()

        def flt(id_: str, default: float) -> float:
            try:
                return float(f(id_))
            except ValueError:
                return default

        def integer(id_: str, default: int) -> int:
            try:
                return int(f(id_))
            except ValueError:
                return default

        def sw(id_: str) -> bool:
            return self.query_one(f"#{id_}", Switch).value

        def sel(id_: str) -> str:
            return str(self.query_one(f"#{id_}", Select).value)

        cfg = AppConfig(
            account=C.AccountConfig(
                username=f("username"),
                password=self.query_one("#password", Input).value,
                auto_login=sw("auto_login"),
                reuse_session=sw("reuse_session"),
            ),
            journey=C.JourneyConfig(
                from_station=f("from_station"),
                to_station=f("to_station"),
                journey_date=f("journey_date"),
                travel_class=sel("travel_class"),
                quota=sel("quota"),
                train_number=f("train_number"),
            ),
            passengers=list(self._passengers),
            timing=C.TimingConfig(
                check_interval_seconds=flt("check_interval", 15.0),
                jitter_seconds=flt("jitter", 3.0),
                start_time=f("start_time"),
                max_attempts=integer("max_attempts", 0),
                retry_on_error=sw("retry_on_error"),
            ),
            behavior=C.BehaviorConfig(
                auto_book_when_available=sw("auto_book"),
                headed=sw("headed"),
                browser=sel("browser"),
                slow_mo_ms=integer("slow_mo", 0),
                save_screenshots=sw("save_screenshots"),
                contact_mobile=f("contact_mobile"),
                upi_id=f("upi_id"),
            ),
        )
        return cfg

    def action_save(self) -> None:
        cfg = self._collect_config()
        cfg.save(self.config_path)
        self.cfg = cfg
        self._log_line(f"Config saved to {self.config_path}", Level.SUCCESS)
        self.notify("Config saved.")

    def action_load(self) -> None:
        self.cfg = AppConfig.load(self.config_path)
        self._passengers = list(self.cfg.passengers)
        self._rebuild_passenger_table()
        self._log_line("Reloaded config from disk. Reopen tabs to see values.", Level.INFO)
        self.notify("Config reloaded (field widgets keep their current values).")

    def action_validate(self) -> None:
        problems = self._collect_config().validate()
        if not problems:
            self._log_line("Validation passed — good to go.", Level.SUCCESS)
            self.notify("Looks good ✅")
            return
        for p in problems:
            self._log_line(f"✗ {p}", Level.ERROR)
        self.notify(f"{len(problems)} problem(s) — see the log.", severity="error")

    @on(Button.Pressed, "#save")
    def _btn_save(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#validate")
    def _btn_validate(self) -> None:
        self.action_validate()

    # ------------------------------------------------------------------ #
    # Run control
    # ------------------------------------------------------------------ #

    @on(Button.Pressed, "#start")
    def _btn_start(self) -> None:
        self.action_start()

    @on(Button.Pressed, "#stop")
    def _btn_stop(self) -> None:
        self.action_stop()

    @on(Button.Pressed, "#close_browser")
    def _btn_close_browser(self) -> None:
        self.run_worker(self._close_bot(), exclusive=False)

    def action_start(self) -> None:
        if self._worker is not None and self._worker.is_running:
            self.notify("Already running.", severity="warning")
            return
        cfg = self._collect_config()
        problems = cfg.validate()
        if problems:
            for p in problems:
                self._log_line(f"✗ {p}", Level.ERROR)
            self.notify("Fix the problems in the log first.", severity="error")
            self.get_child_by_type(TabbedContent).active = "tab-run"
            return
        cfg.save(self.config_path)
        self.cfg = cfg
        self.get_child_by_type(TabbedContent).active = "tab-run"
        self._log_line("Starting booking run…", Level.SUCCESS)
        self._bot = IRCTCBot(cfg, on_event=self._on_bot_event)
        self._worker = self.run_worker(self._run_bot(), exclusive=True, name="booking")

    def action_stop(self) -> None:
        if self._bot is not None:
            self._bot.stop()
            self._log_line("Stop requested — finishing current step…", Level.WARN)
            self.notify("Stopping…")
        else:
            self.notify("Nothing is running.")

    async def _run_bot(self) -> None:
        assert self._bot is not None
        try:
            await self._bot.run()
        except Exception as exc:  # noqa: BLE001
            self._log_line(f"Run crashed: {exc}", Level.ERROR)

    async def _close_bot(self) -> None:
        if self._bot is not None:
            await self._bot.close()
            self._log_line("Browser closed.", Level.INFO)
            self.notify("Browser closed.")

    # ------------------------------------------------------------------ #
    # Event sink from the bot
    # ------------------------------------------------------------------ #

    async def _on_bot_event(self, event: BotEvent) -> None:
        if event.message and event.kind != "countdown":
            self._log_line(event.message, event.level)

        if event.phase is not None:
            self._safe_update("#status_phase", f"Phase: {event.phase.value}")

        data = event.data or {}
        if "attempts" in data:
            self._safe_update("#status_attempts", f"Attempts: {data['attempts']}")
        if event.kind == "status" and "availability" in data:
            raw = data.get("raw") or data["availability"]
            self._safe_update("#status_avail", f"Availability: {raw}")
        if event.kind == "countdown":
            self._safe_update("#status_countdown", f"Next: {event.message}")
        if event.phase in (Phase.AVAILABLE, Phase.BOOKING, Phase.PASSENGERS, Phase.HANDOFF, Phase.DONE):
            # Make sure the user sees these prominently in the log too.
            if event.kind == "phase":
                self._log_line(f"» {event.message}", event.level)

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #

    def _safe_update(self, selector: str, text: str) -> None:
        try:
            self.query_one(selector, Static).update(text)
        except Exception:  # noqa: BLE001
            pass

    def _log_line(self, message: str, level: Level = Level.INFO) -> None:
        style = LEVEL_MARKUP.get(level, "white")
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            log = self.query_one("#log", RichLog)
        except Exception:  # noqa: BLE001
            return
        log.write(f"[dim]{stamp}[/dim] [{style}]{_escape(message)}[/{style}]")

    async def action_quit(self) -> None:  # type: ignore[override]
        if self._bot is not None:
            self._bot.stop()
            await self._bot.close()
        self.exit()


def _escape(text: str) -> str:
    """Escape Rich markup so user/bot text can't break the log rendering."""
    return text.replace("[", r"\[")


def run(config_path: str | Path | None = None) -> None:
    IRCTCApp(config_path=config_path).run()

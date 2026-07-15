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

import asyncio
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
from . import stations
from .alarm import AlarmPlayer
from .automation import IRCTCBot
from .config import AppConfig, Passenger
from .events import BotEvent, Level, Phase
from .notify import TelegramNotifier
from .preflight import run_preflight

# Phases at which the completion alarm should start ringing.
_ALARM_PHASES = {Phase.AVAILABLE, Phase.BOOKING, Phase.PASSENGERS, Phase.HANDOFF, Phase.DONE}
# Phases that trigger a Telegram alert (once each).
_NOTIFY_PHASES = {Phase.AVAILABLE, Phase.HANDOFF, Phase.DONE, Phase.STOPPED, Phase.ERROR}

_TG_HELP = (
    "🚆 IRCTC TUI — remote commands:\n"
    "• status — current phase / attempts\n"
    "• run — start the booking run\n"
    "• stop — stop the run\n"
    "• silence — silence the alarm\n"
    "• shot — screenshot the live browser\n"
    "• help — this message"
)

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
        ("ctrl+g", "silence", "Silence alarm"),
        ("f2", "preflight", "Pre-flight"),
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
        self._preflight_worker: Worker | None = None
        self._alarm: AlarmPlayer | None = None
        self._notifier: TelegramNotifier | None = None
        self._notified_texts: set[str] = set()
        self._tg_listening: bool = False
        self._tg_offset: int | None = None

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
            with TabPane("Telegram", id="tab-telegram"):
                yield from self._telegram_tab()
            with TabPane("Run", id="tab-run"):
                yield from self._run_tab()
        yield Footer()

    # -- tab builders ---------------------------------------------------- #

    def _journey_tab(self) -> ComposeResult:
        j = self.cfg.journey
        yield Static("Where and when", classes="section-title")
        yield Static(
            "Pick your stations from the dropdowns. Open one and type to jump "
            "(e.g. 'TIR' → Tirupati). Missing one? Add it to stations.py.",
            classes="hint",
        )
        with Grid(classes="form-grid"):
            yield Label("From station", classes="field-label")
            yield Select(stations.options(j.from_station), value=j.from_station,
                         id="from_station", allow_blank=False)
            yield Label("To station", classes="field-label")
            yield Select(stations.options(j.to_station), value=j.to_station,
                         id="to_station", allow_blank=False)
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
            yield Label("Alarm on success", classes="field-label")
            yield Switch(value=b.alarm_on_success, id="alarm_on_success")
            yield Label("Alarm sound file", classes="field-label")
            yield Input(value=b.alarm_sound_path, id="alarm_sound_path",
                        placeholder="blank = built-in tune; or path to your .wav/.mp3")

    def _telegram_tab(self) -> ComposeResult:
        t = self.cfg.telegram
        yield Static("Telegram alerts", classes="section-title")
        yield Static(
            "Get pinged when a seat is found / it's time to pay. "
            "Token from @BotFather; owner id (numeric) from @userinfobot. "
            "Both are stored in the git-ignored config — treat the token like a password.",
            classes="hint",
        )
        with Grid(classes="form-grid"):
            yield Label("Enabled", classes="field-label")
            yield Switch(value=t.enabled, id="tg_enabled")
            yield Label("Bot token", classes="field-label")
            yield Input(value=t.bot_token, id="tg_token", password=True,
                        placeholder="123456:ABC-DEF… from @BotFather")
            yield Label("Owner id", classes="field-label")
            yield Input(value=t.owner_id, id="tg_owner", placeholder="your numeric chat id")
        with Horizontal(id="telegram-buttons"):
            yield Button("📤 Test Telegram", id="test_telegram", variant="primary")

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
                yield Button("🔕 Silence", id="silence_alarm", variant="warning")
                yield Button("💾 Save", id="save")
            with Horizontal(id="run-buttons-2"):
                yield Button("🔎 Pre-flight", id="preflight", variant="primary")
                yield Button("✓ Validate", id="validate")
                yield Button("🔔 Test alarm", id="test_alarm")
                yield Button("✕ Close browser", id="close_browser")
            yield Static("🚆 Trains found (updates each check)", classes="section-title")
            yield DataTable(id="results_table", cursor_type="row")
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        table = self.query_one("#passenger_table", DataTable)
        table.add_columns("Name", "Age", "Gender", "Berth", "Food")
        self._rebuild_passenger_table()
        self.query_one("#results_table", DataTable).add_columns(
            "Train", "No.", "Dep", "Arr", "Availability"
        )
        self._log_line("Welcome. Fill the tabs, then press Start (Ctrl+R).", Level.INFO)
        self._log_line(
            "Reminder: you solve the CAPTCHA, login, and payment in the browser.",
            Level.HUMAN,
        )
        if self.config_path.exists():
            self._log_line(f"Loaded config from {self.config_path}", Level.SUCCESS)
        # Bring Telegram remote control online if it's already configured.
        tg = self.cfg.telegram
        if tg.enabled and tg.bot_token and tg.owner_id:
            self._notifier = TelegramNotifier(tg.bot_token, tg.owner_id, enabled=True)
            self._ensure_tg_listener()

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
                from_station=sel("from_station"),
                to_station=sel("to_station"),
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
                alarm_on_success=sw("alarm_on_success"),
                alarm_sound_path=f("alarm_sound_path"),
            ),
            telegram=C.TelegramConfig(
                enabled=sw("tg_enabled"),
                bot_token=self.query_one("#tg_token", Input).value.strip(),
                owner_id=f("tg_owner"),
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

    @on(Button.Pressed, "#test_alarm")
    def _btn_test_alarm(self) -> None:
        path = self.query_one("#alarm_sound_path", Input).value.strip()
        self._start_alarm(path)
        self._log_line("Testing alarm — press 🔕 Silence (Ctrl+G) to stop.", Level.HUMAN)

    @on(Button.Pressed, "#silence_alarm")
    def _btn_silence_alarm(self) -> None:
        self.action_silence()

    def _start_alarm(self, sound_path: str = "") -> None:
        if self._alarm is not None and self._alarm.playing:
            return
        try:
            if self._alarm is not None:
                self._alarm.stop()
            self._alarm = AlarmPlayer(sound_path or None)
            if not self._alarm.available:
                self._log_line("No audio player found; alarm will use the terminal bell.", Level.WARN)
            self._alarm.start()
        except Exception as exc:  # noqa: BLE001
            self._log_line(f"Could not start alarm: {exc}", Level.ERROR)

    def action_silence(self) -> None:
        if self._alarm is not None and self._alarm.playing:
            self._alarm.stop()
            self._log_line("Alarm silenced.", Level.INFO)
            self.notify("Alarm silenced.")
        else:
            self.notify("No alarm is ringing.")

    # ------------------------------------------------------------------ #
    # Pre-flight selector check
    # ------------------------------------------------------------------ #

    @on(Button.Pressed, "#preflight")
    def _btn_preflight(self) -> None:
        self.action_preflight()

    def action_preflight(self) -> None:
        if self._preflight_worker is not None and self._preflight_worker.is_running:
            self.notify("Pre-flight already running.", severity="warning")
            return
        cfg = self._collect_config()
        self.get_child_by_type(TabbedContent).active = "tab-run"
        self._log_line("Pre-flight: verifying selectors against the live IRCTC site…", Level.INFO)
        self._preflight_worker = self.run_worker(
            self._run_preflight(cfg), exclusive=False, name="preflight"
        )

    async def _run_preflight(self, cfg: AppConfig) -> None:
        try:
            await run_preflight(cfg, self._on_bot_event)
        except Exception as exc:  # noqa: BLE001
            self._log_line(f"Pre-flight crashed: {exc}", Level.ERROR)

    # ------------------------------------------------------------------ #
    # Telegram
    # ------------------------------------------------------------------ #

    @on(Button.Pressed, "#test_telegram")
    def _btn_test_telegram(self) -> None:
        token = self.query_one("#tg_token", Input).value.strip()
        owner = self.query_one("#tg_owner", Input).value.strip()
        if not token or not owner:
            self.notify("Enter the bot token and owner id first.", severity="error")
            return
        self._log_line("Sending a Telegram test message…", Level.INFO)
        self.run_worker(self._test_telegram(token, owner), exclusive=False)

    async def _test_telegram(self, token: str, owner: str) -> None:
        notifier = TelegramNotifier(token, owner, enabled=True)
        ok, detail = await notifier.send("✅ IRCTC Tatkal TUI — your Telegram alerts are working!")
        if ok:
            self._log_line("Telegram test sent ✓ — check your chat.", Level.SUCCESS)
            self.notify("Telegram test sent.")
            # Creds verified — bring remote control online now.
            self._notifier = notifier
            self._ensure_tg_listener()
        else:
            self._log_line(f"Telegram test failed: {detail[:160]}", Level.ERROR)
            self.notify("Telegram test failed — see the log.", severity="error")

    async def _send_telegram(self, text: str) -> None:
        if self._notifier is not None and self._notifier.enabled:
            ok, detail = await self._notifier.send(text)
            if not ok:
                self._log_line(f"Telegram send failed: {detail[:120]}", Level.WARN)

    async def _maybe_notify(self, event: BotEvent) -> None:
        if self._notifier is None or not self._notifier.enabled or not event.message:
            return
        trigger = (event.kind == "phase" and event.phase in _NOTIFY_PHASES) or event.level == Level.HUMAN
        if not trigger:
            return
        text = f"🚆 IRCTC TUI — {event.message}"
        if text in self._notified_texts:
            return
        self._notified_texts.add(text)
        await self._send_telegram(text)

    # ------------------------------------------------------------------ #
    # Two-way Telegram control
    # ------------------------------------------------------------------ #

    def _ensure_tg_listener(self) -> None:
        """Start the command-polling worker once, if Telegram is configured."""
        if self._tg_listening or self._notifier is None or not self._notifier.enabled:
            return
        self._tg_listening = True
        self.run_worker(self._telegram_listen(), exclusive=False, name="tg_listen")
        self._log_line("Telegram remote control online — send 'help' to your bot.", Level.INFO)

    async def _telegram_listen(self) -> None:
        notifier = self._notifier
        if notifier is None:
            self._tg_listening = False
            return
        # Skip messages sent before we came online so stale commands don't fire.
        try:
            stale = await notifier.get_updates()
            self._tg_offset = (stale[-1]["update_id"] + 1) if stale else None
        except Exception:  # noqa: BLE001
            self._tg_offset = None
        await notifier.send("🤖 IRCTC TUI remote control online. Send 'help' for commands.")
        while self._tg_listening:
            try:
                updates = await notifier.get_updates(offset=self._tg_offset, timeout=0)
                for update in updates:
                    self._tg_offset = update["update_id"] + 1
                    message = update.get("message") or {}
                    chat_id = str((message.get("chat") or {}).get("id"))
                    body = (message.get("text") or "").strip()
                    if not body:
                        continue
                    if notifier.owner_id and chat_id != notifier.owner_id:
                        continue  # only the owner may control the run
                    self._log_line(f"Telegram ⟵ {body}", Level.INFO)
                    reply = await self._handle_tg_command(body)
                    if reply:
                        await notifier.send(reply)
            except Exception as exc:  # noqa: BLE001 - never let polling kill the app
                self._log_line(f"Telegram poll error: {exc}", Level.WARN)
            await asyncio.sleep(3.0)

    async def _handle_tg_command(self, text: str) -> str:
        parts = text.strip().lstrip("/").split()
        cmd = parts[0].lower() if parts else ""
        if cmd in ("status", "state"):
            return self._status_text()
        if cmd == "stop":
            self.action_stop()
            return "🛑 Stop requested."
        if cmd in ("run", "book", "go"):
            if self._worker is not None and self._worker.is_running:
                return "Already running."
            self.action_start()
            running = self._worker is not None and self._worker.is_running
            return "▶️ Booking run started." if running else "Couldn't start — config invalid (check the app)."
        if cmd in ("silence", "mute", "stopalarm"):
            self.action_silence()
            return "🔕 Alarm silenced."
        if cmd in ("shot", "screenshot", "pic", "photo"):
            return await self._send_browser_shot()
        if cmd in ("help", "start", "commands", "?"):
            return _TG_HELP
        return f"Unknown command '{cmd}'. Send 'help' for the list."

    def _status_text(self) -> str:
        if self._bot is None:
            return "📊 Status: idle (no run active). Send 'run' to start."
        running = self._worker is not None and self._worker.is_running
        return (
            "📊 IRCTC TUI status\n"
            f"Phase: {self._bot.phase.value}\n"
            f"Attempts: {self._bot.attempts}\n"
            f"Running: {'yes' if running else 'no'}\n"
            f"Logged in: {'yes' if self._bot.logged_in else 'no'}"
        )

    async def _send_browser_shot(self) -> str:
        if self._bot is None or self._bot.page is None:
            return "No browser is open to screenshot."
        if self._notifier is None:
            return "Telegram is not configured."
        path = self.config_path.parent / ".tg_shot.png"
        try:
            await self._bot.page.screenshot(path=str(path))
        except Exception as exc:  # noqa: BLE001
            return f"Screenshot failed: {exc}"
        ok, detail = await self._notifier.send_photo(str(path), caption="Current browser view")
        return "" if ok else f"Screenshot send failed: {detail[:100]}"

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
        # (Re)build the Telegram notifier for this run.
        self._notifier = TelegramNotifier(
            cfg.telegram.bot_token, cfg.telegram.owner_id, enabled=cfg.telegram.enabled
        )
        self._notified_texts.clear()
        if self._notifier.enabled:
            j = cfg.journey
            route = f"{j.from_station}→{j.to_station} {j.journey_date} {j.quota}/{j.travel_class}"
            self._log_line("Telegram alerts enabled for this run.", Level.INFO)
            self.run_worker(self._send_telegram(f"▶️ IRCTC TUI booking started: {route}"),
                            exclusive=False)
            self._ensure_tg_listener()
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
        if event.kind == "results":
            self._update_results_table(data.get("trains", []), data.get("target_class", ""))
        if event.phase in _ALARM_PHASES:
            # Make sure the user sees these prominently in the log too.
            if event.kind == "phase":
                self._log_line(f"» {event.message}", event.level)
            # Ring the completion alarm the moment a seat is within reach.
            if self.cfg.behavior.alarm_on_success:
                already = self._alarm is not None and self._alarm.playing
                self._start_alarm(self.cfg.behavior.alarm_sound_path)
                if not already:
                    self._log_line(
                        "🔔 ALARM RINGING — come to the browser. Press 🔕 Silence (Ctrl+G) to stop.",
                        Level.HUMAN,
                    )

        # Fire a Telegram alert for key moments (once each).
        await self._maybe_notify(event)

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #

    def _safe_update(self, selector: str, text: str) -> None:
        try:
            self.query_one(selector, Static).update(text)
        except Exception:  # noqa: BLE001
            pass

    def _update_results_table(self, trains: list[dict], target_class: str) -> None:
        try:
            table = self.query_one("#results_table", DataTable)
        except Exception:  # noqa: BLE001
            return
        table.clear()
        tc = (target_class or "").upper()
        for t in trains:
            classes = t.get("classes", [])
            target = next((c for c in classes if c.get("class_code") == tc), None)
            mark = "✓ " if (target and target.get("bookable")) else ""
            avail = "  ".join(
                f"{c.get('class_code')}:{c.get('status_raw') or c.get('availability')}"
                for c in classes
            )
            table.add_row(
                f"{mark}{t.get('name', '')}".strip(),
                t.get("number", ""),
                t.get("departure", ""),
                t.get("arrival", ""),
                avail or "—",
            )

    def _log_line(self, message: str, level: Level = Level.INFO) -> None:
        style = LEVEL_MARKUP.get(level, "white")
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            log = self.query_one("#log", RichLog)
        except Exception:  # noqa: BLE001
            return
        log.write(f"[dim]{stamp}[/dim] [{style}]{_escape(message)}[/{style}]")

    async def action_quit(self) -> None:  # type: ignore[override]
        self._tg_listening = False
        if self._alarm is not None:
            self._alarm.stop()
        if self._bot is not None:
            self._bot.stop()
            await self._bot.close()
        self.exit()


def _escape(text: str) -> str:
    """Escape Rich markup so user/bot text can't break the log rendering."""
    return text.replace("[", r"\[")


def run(config_path: str | Path | None = None) -> None:
    IRCTCApp(config_path=config_path).run()

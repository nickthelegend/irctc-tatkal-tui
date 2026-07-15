"""Playwright automation engine for IRCTC.

Responsibilities
----------------
* Launch a **headed** browser you can watch and grab at any time.
* Optionally pre-fill the login form, then **wait for you** to solve the CAPTCHA
  and press SIGN IN. The engine never solves a CAPTCHA.
* Fill the search form (stations, date, quota, class) and submit.
* Poll availability on a configurable interval until a seat opens (or you stop).
* When bookable, click *Book Now* and fill the passenger form.
* **Stop before payment.** The engine detects the payment/CAPTCHA hand-off and
  waits — you complete payment yourself.

Everything the engine does is reported through :class:`~irctc_tui.events.BotEvent`
callbacks so the TUI (or any other front-end) can render live progress.

The engine is intentionally defensive: IRCTC's DOM shifts often, so selector
lookups try several candidates (see :mod:`~irctc_tui.selectors`) and failures are
logged rather than fatal wherever a human could reasonably take over.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    async_playwright,
)
from playwright.async_api import TimeoutError as PWTimeout

from . import selectors as S
from .config import AppConfig
from .events import BotEvent, Level, Phase

EventSink = Callable[[BotEvent], Awaitable[None] | None]

_MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


class IRCTCBot:
    """Drives a single booking session. Create one, ``await run()``, ``stop()`` to halt."""

    def __init__(
        self,
        config: AppConfig,
        on_event: EventSink | None = None,
        *,
        screenshot_dir: str | Path = "screenshots",
        storage_state_path: str | Path = "storage_state.json",
    ) -> None:
        self.config = config
        self.on_event = on_event
        self.screenshot_dir = Path(screenshot_dir)
        self.storage_state_path = Path(storage_state_path)

        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

        self._stop = asyncio.Event()
        self.phase: Phase = Phase.IDLE
        self.attempts = 0
        self.logged_in = False

    # ------------------------------------------------------------------ #
    # Event helpers
    # ------------------------------------------------------------------ #

    async def _emit(self, event: BotEvent) -> None:
        if event.phase is not None:
            self.phase = event.phase
        if self.on_event is None:
            return
        result = self.on_event(event)
        if inspect.isawaitable(result):
            await result

    async def _log(self, message: str, level: Level = Level.INFO) -> None:
        await self._emit(BotEvent(kind="log", message=message, level=level))

    async def _phase(self, phase: Phase, message: str = "", level: Level = Level.INFO) -> None:
        await self._emit(BotEvent(kind="phase", message=message or phase.value, level=level, phase=phase))

    async def _human(self, message: str) -> None:
        """Ask the user to do something in the browser now."""
        await self._emit(BotEvent(kind="log", message=message, level=Level.HUMAN))

    # ------------------------------------------------------------------ #
    # Public control
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        """Request a graceful stop; the run loop notices at the next checkpoint."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    async def run(self) -> None:
        """Top-level orchestration. Safe to call once per bot instance."""
        try:
            await self._launch()
            await self._open_search()

            if self.config.account.auto_login and self.config.account.username.strip():
                await self._login()

            await self._wait_until_start_time()

            booked = await self._poll_loop()

            if booked:
                await self._book()
        except _StopRequested:
            await self._phase(Phase.STOPPED, "Stopped by user.", Level.WARN)
        except Exception as exc:  # noqa: BLE001 - surface everything to the UI
            await self._phase(Phase.ERROR, f"Error: {exc}", Level.ERROR)
            await self._screenshot("error")
        finally:
            # Never auto-close the browser: the user may still be finishing
            # login or payment. Closing is explicit via close().
            await self._log("Run finished. Browser left open for you.", Level.INFO)

    async def close(self) -> None:
        """Tear down Playwright. Call when the user is truly done."""
        try:
            if self.config.account.reuse_session and self._context is not None:
                try:
                    await self._context.storage_state(path=str(self.storage_state_path))
                except Exception:  # noqa: BLE001
                    pass
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        finally:
            self._browser = self._context = self.page = self._pw = None

    # ------------------------------------------------------------------ #
    # Steps
    # ------------------------------------------------------------------ #

    async def _launch(self) -> None:
        await self._phase(Phase.LAUNCHING, "Launching browser…")
        b = self.config.behavior
        self._pw = await async_playwright().start()
        launcher = getattr(self._pw, b.browser, self._pw.chromium)
        self._browser = await launcher.launch(
            headless=not b.headed,
            slow_mo=b.slow_mo_ms,
            args=["--start-maximized"] if b.headed else [],
        )
        context_kwargs: dict = {"no_viewport": b.headed}
        if self.config.account.reuse_session and self.storage_state_path.exists():
            context_kwargs["storage_state"] = str(self.storage_state_path)
            await self._log("Reusing saved browser session.", Level.INFO)
        self._context = await self._browser.new_context(**context_kwargs)
        self.page = await self._context.new_page()
        await self._log(f"{b.browser} launched ({'headed' if b.headed else 'headless'}).", Level.SUCCESS)

    async def _open_search(self) -> None:
        assert self.page is not None
        await self._log(f"Opening {S.SEARCH_URL}")
        await self.page.goto(S.SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
        await self._dismiss_popups()
        await self._screenshot("search-page")

    async def _login(self) -> None:
        assert self.page is not None
        if await self._is_logged_in():
            self.logged_in = True
            await self._log("Already logged in (saved session).", Level.SUCCESS)
            return

        await self._phase(Phase.LOGIN, "Opening login…")
        await self._click_first(S.LOGIN_NAV_BUTTON, what="LOGIN button")
        await asyncio.sleep(1.0)

        acc = self.config.account
        if acc.username:
            await self._fill_first(S.USERNAME_INPUT, acc.username, what="username")
        if acc.password:
            await self._fill_first(S.PASSWORD_INPUT, acc.password, what="password")
            await self._log("Password pre-filled from config.", Level.WARN)

        await self._human(
            "Solve the CAPTCHA in the browser and press SIGN IN. "
            "I'll wait here until you're logged in."
        )
        await self._wait_for_login()

    async def _wait_for_login(self, timeout_s: float = 600.0) -> None:
        """Block until a logged-in marker appears or the user stops."""
        deadline = _loop_time() + timeout_s
        while _loop_time() < deadline:
            self._raise_if_stopping()
            if await self._is_logged_in():
                self.logged_in = True
                await self._phase(Phase.SEARCHING, "Logged in.", Level.SUCCESS)
                await self._screenshot("logged-in")
                return
            await asyncio.sleep(2.0)
        await self._log("Login wait timed out — continuing anyway.", Level.WARN)

    async def _wait_until_start_time(self) -> None:
        start = self.config.timing.start_time.strip()
        if not start:
            return
        try:
            target_t = datetime.strptime(start, "%H:%M:%S").time()
        except ValueError:
            await self._log(f"Bad start_time '{start}', ignoring.", Level.WARN)
            return

        await self._phase(Phase.WAITING_START, f"Holding until {start}…")
        while True:
            self._raise_if_stopping()
            now = datetime.now()
            if now.time() >= target_t:
                await self._log(f"Start time {start} reached — go!", Level.SUCCESS)
                return
            remaining = (
                datetime.combine(now.date(), target_t) - now
            ).total_seconds()
            await self._emit(
                BotEvent(
                    kind="countdown",
                    message=f"Starting in {int(remaining)}s (at {start})",
                    phase=Phase.WAITING_START,
                    data={"remaining": int(remaining), "reason": "start_time"},
                )
            )
            await asyncio.sleep(min(1.0, max(0.2, remaining)))

    async def _poll_loop(self) -> bool:
        """Search + check availability repeatedly. Return True when bookable."""
        t = self.config.timing
        await self._phase(Phase.POLLING, "Polling availability…")
        while True:
            self._raise_if_stopping()
            self.attempts += 1
            if t.max_attempts and self.attempts > t.max_attempts:
                await self._log(f"Reached max attempts ({t.max_attempts}). Stopping.", Level.WARN)
                return False

            await self._emit(
                BotEvent(kind="status", message=f"Attempt #{self.attempts}", phase=Phase.POLLING,
                         data={"attempts": self.attempts})
            )
            try:
                await self._fill_and_submit_search()
                status, raw, train = await self._read_target_availability()
                await self._emit(
                    BotEvent(
                        kind="status",
                        message=f"{train or 'target'}: {raw or status.value}",
                        level=Level.SUCCESS if status.bookable else Level.INFO,
                        phase=Phase.POLLING,
                        data={"availability": status.value, "raw": raw, "train": train},
                    )
                )
                if status.bookable:
                    await self._phase(Phase.AVAILABLE, f"Bookable! {raw or status.value}", Level.SUCCESS)
                    await self._screenshot("available")
                    if self.config.behavior.auto_book_when_available:
                        return True
                    await self._human("Seat is available — take over in the browser to book.")
                    return False
            except _StopRequested:
                raise
            except Exception as exc:  # noqa: BLE001
                await self._log(f"Attempt failed: {exc}", Level.ERROR)
                await self._screenshot(f"attempt-{self.attempts}-error")
                if not t.retry_on_error:
                    raise

            await self._sleep_interval()

    async def _book(self) -> None:
        await self._phase(Phase.BOOKING, "Clicking Book Now…")
        clicked = await self._click_first(S.BOOK_NOW_BUTTON, what="Book Now", required=False)
        if not clicked:
            await self._human("Couldn't find 'Book Now' — click it yourself; I'll continue.")
        await asyncio.sleep(2.0)

        # If login is demanded now (Book Now often triggers it), wait for the human.
        if not await self._is_logged_in():
            await self._human("Login required — sign in (with CAPTCHA) in the browser.")
            await self._wait_for_login()

        await self._fill_passengers()
        await self._wait_for_payment_handoff()

    async def _fill_passengers(self) -> None:
        assert self.page is not None
        await self._phase(Phase.PASSENGERS, "Filling passenger details…")
        # Give the passenger page a moment to render.
        try:
            await self.page.wait_for_url(f"**{S.BOOKING_URL_FRAGMENT}**", timeout=15_000)
        except PWTimeout:
            await self._log("Passenger page URL not detected; trying anyway.", Level.WARN)

        for i, passenger in enumerate(self.config.passengers):
            self._raise_if_stopping()
            if i > 0:
                await self._click_first(S.ADD_PASSENGER_LINK, what="Add Passenger", required=False)
                await asyncio.sleep(0.5)
            await self._fill_one_passenger(i, passenger)

        # Contact mobile (once).
        if self.config.behavior.contact_mobile:
            await self._fill_first(S.MOBILE_INPUT, self.config.behavior.contact_mobile,
                                   what="mobile", required=False)

        await self._screenshot("passengers-filled")
        await self._log("Passenger details filled. Review them in the browser.", Level.SUCCESS)

    async def _fill_one_passenger(self, index: int, p) -> None:
        n = index + 1
        # Target the Nth instance of each field so multiple passengers work.
        await self._fill_nth(S.PASSENGER_NAME_INPUT, index, p.name, what=f"passenger {n} name")
        await self._fill_nth(S.PASSENGER_AGE_INPUT, index, str(p.age), what=f"passenger {n} age")
        await self._select_nth(S.PASSENGER_GENDER_SELECT, index, p.gender, what=f"passenger {n} gender")
        if p.berth_preference and p.berth_preference != "No Preference":
            await self._select_nth(S.PASSENGER_BERTH_SELECT, index, p.berth_preference,
                                   what=f"passenger {n} berth", required=False)
        if p.food_preference and p.food_preference != "No Food":
            await self._select_nth(S.PASSENGER_FOOD_SELECT, index, p.food_preference,
                                   what=f"passenger {n} food", required=False)
        await self._log(f"Passenger {n}: {p.summary()}", Level.INFO)

    async def _wait_for_payment_handoff(self, timeout_s: float = 900.0) -> None:
        """Detect the review/payment stage and hand control to the human."""
        assert self.page is not None
        await self._phase(Phase.HANDOFF, "Continuing to review / payment…")
        # Try to advance from passenger page to the review page once.
        await self._click_first(S.PASSENGER_CONTINUE_BUTTON, what="Continue", required=False)
        await asyncio.sleep(2.0)

        await self._human(
            "STOP — this is where I hand off. Solve the CAPTCHA and complete "
            "payment YOURSELF in the browser. I will not touch payment."
        )
        if self.config.behavior.upi_id:
            await self._log(f"Your saved UPI id (for convenience): {self.config.behavior.upi_id}", Level.INFO)
        await self._screenshot("handoff")

        # Passively watch for the payment page so the UI can confirm we're there.
        deadline = _loop_time() + timeout_s
        while _loop_time() < deadline:
            self._raise_if_stopping()
            if await self._any_visible(S.PAYMENT_PAGE_MARKERS, timeout=1_000):
                await self._phase(Phase.DONE, "Payment page reached — it's all you now.", Level.SUCCESS)
                return
            await asyncio.sleep(2.0)
        await self._phase(Phase.DONE, "Handed off. Finish in the browser.", Level.SUCCESS)

    # ------------------------------------------------------------------ #
    # Search form filling
    # ------------------------------------------------------------------ #

    async def _fill_and_submit_search(self) -> None:
        assert self.page is not None
        j = self.config.journey
        # Re-open the search page if we've navigated away (e.g. previous attempt).
        if S.BOOKING_URL_FRAGMENT in (self.page.url or ""):
            await self.page.goto(S.SEARCH_URL, wait_until="domcontentloaded")
            await self._dismiss_popups()

        await self._select_station(S.FROM_STATION_INPUT, j.from_station, what="From station")
        await self._select_station(S.TO_STATION_INPUT, j.to_station, what="To station")
        await self._set_journey_date(j.journey_date)
        await self._select_dropdown(S.QUOTA_DROPDOWN, j.quota, what="quota")
        if j.travel_class:
            await self._select_dropdown(S.CLASS_DROPDOWN, _class_label(j.travel_class),
                                        what="class", required=False)
        await self._click_first(S.SEARCH_BUTTON, what="Search")
        await asyncio.sleep(2.5)  # let results render

    async def _select_station(self, input_candidates: list[str], term: str, *, what: str) -> None:
        assert self.page is not None
        loc = await self._first(input_candidates, what=what)
        if loc is None:
            raise RuntimeError(f"Could not find {what} input.")
        await loc.click()
        await loc.fill("")
        await loc.type(term, delay=80)
        # Wait for suggestions then pick the top one.
        picked = False
        try:
            await self.page.wait_for_selector(_join(S.AUTOCOMPLETE_ITEMS), timeout=4_000)
            first_item = self.page.locator(_join(S.AUTOCOMPLETE_ITEMS)).first
            await first_item.click(timeout=2_000)
            picked = True
        except PWTimeout:
            pass
        if not picked:
            await loc.press("ArrowDown")
            await loc.press("Enter")
        await self._log(f"{what}: '{term}' selected.", Level.INFO)

    async def _set_journey_date(self, ddmmyyyy: str) -> None:
        """Set the journey date. Try typing first, fall back to calendar nav."""
        assert self.page is not None
        loc = await self._first(S.JOURNEY_DATE_INPUT, what="journey date")
        if loc is None:
            raise RuntimeError("Could not find journey date field.")
        # Fast path: type it.
        try:
            await loc.click()
            await loc.fill("")
            await loc.type(ddmmyyyy, delay=50)
            await self.page.keyboard.press("Escape")
            value = (await loc.input_value()) or ""
            if _digits(value) == _digits(ddmmyyyy):
                await self._log(f"Journey date typed: {ddmmyyyy}", Level.INFO)
                return
        except Exception:  # noqa: BLE001
            pass
        # Fallback: navigate the PrimeNG calendar.
        await self._pick_date_via_calendar(ddmmyyyy)

    async def _pick_date_via_calendar(self, ddmmyyyy: str) -> None:
        assert self.page is not None
        day, month, year = (int(x) for x in ddmmyyyy.split("-"))
        target_label = f"{_MONTHS[month - 1]} {year}"
        loc = await self._first(S.JOURNEY_DATE_INPUT, what="journey date")
        if loc:
            await loc.click()
        await self.page.wait_for_selector(_join(S.CALENDAR_PANEL), timeout=5_000)
        for _ in range(24):  # cap navigation to avoid infinite loops
            self._raise_if_stopping()
            shown = await self._calendar_label()
            if shown == target_label:
                break
            # Decide direction from month/year comparison.
            forward = _label_is_before(shown, target_label)
            await self._click_first(S.CALENDAR_NEXT if forward else S.CALENDAR_PREV,
                                    what="calendar nav", required=False)
            await asyncio.sleep(0.3)
        # Click the day cell whose text equals the day number.
        cells = self.page.locator(_join(S.CALENDAR_DAY_CELLS))
        count = await cells.count()
        for i in range(count):
            cell = cells.nth(i)
            txt = (await cell.inner_text()).strip()
            if txt == str(day):
                await cell.click()
                await self._log(f"Journey date picked via calendar: {ddmmyyyy}", Level.INFO)
                return
        await self._log(f"Could not click day {day} in calendar.", Level.WARN)

    async def _calendar_label(self) -> str:
        month = await self._text_of(S.CALENDAR_MONTH_LABEL)
        year = await self._text_of(S.CALENDAR_YEAR_LABEL)
        return f"{month.strip().lower()} {year.strip()}"

    async def _select_dropdown(self, dropdown_candidates: list[str], value: str, *,
                               what: str, required: bool = True) -> None:
        """Open a PrimeNG dropdown and click the option matching ``value``."""
        assert self.page is not None
        trigger = await self._first(dropdown_candidates, what=what, required=required)
        if trigger is None:
            return
        await trigger.click()
        await asyncio.sleep(0.4)
        # Options render in an overlay panel; match by (case-insensitive) text.
        items = self.page.locator(_join(S.DROPDOWN_ITEMS))
        n = await items.count()
        wanted = value.strip().lower()
        for i in range(n):
            item = items.nth(i)
            txt = (await item.inner_text()).strip().lower()
            if wanted in txt or txt in wanted:
                await item.click()
                await self._log(f"{what}: '{value}' selected.", Level.INFO)
                return
        await self._log(f"{what}: option '{value}' not found in dropdown.", Level.WARN)
        # Close the panel so it doesn't block the next click.
        await self.page.keyboard.press("Escape")

    # ------------------------------------------------------------------ #
    # Availability reading
    # ------------------------------------------------------------------ #

    async def _read_target_availability(self):
        """Return ``(Availability, raw_text, train_label)`` for the target class.

        Best-effort: IRCTC's results DOM is intricate and version-dependent. If we
        can't confidently parse, we return UNKNOWN and let the poll loop retry (or
        the user take over in the visible browser).
        """
        assert self.page is not None
        j = self.config.journey
        cards = self.page.locator(_join(S.TRAIN_CARD))
        count = await cards.count()
        if count == 0:
            return S.classify_availability(""), "", ""

        target_indices = range(count)
        train_label = ""
        # If a specific train number is set, narrow to the matching card.
        if j.train_number.strip():
            for i in range(count):
                text = (await cards.nth(i).inner_text()) or ""
                if j.train_number.strip() in text:
                    target_indices = [i]
                    train_label = j.train_number.strip()
                    break

        for i in target_indices:
            card = cards.nth(i)
            # Click the class box matching the desired class code.
            clicked = await self._click_class_in_card(card, j.travel_class)
            if not clicked:
                continue
            await asyncio.sleep(1.2)
            raw = await self._read_first_availability(card)
            status = S.classify_availability(raw)
            label = train_label or await self._train_label_of(card)
            if status is not S.Availability.UNKNOWN:
                return status, raw, label
        return S.classify_availability(""), "", train_label

    async def _click_class_in_card(self, card: Locator, class_code: str) -> bool:
        cells = card.locator(_join(S.CLASS_CELL))
        n = await cells.count()
        wanted = class_code.strip().upper()
        for i in range(n):
            cell = cells.nth(i)
            txt = ((await cell.inner_text()) or "").strip().upper()
            if wanted and wanted in txt:
                try:
                    await cell.click(timeout=3_000)
                    return True
                except Exception:  # noqa: BLE001
                    return False
        return False

    async def _read_first_availability(self, card: Locator) -> str:
        for sel in S.AVAILABILITY_STATUS:
            loc = card.locator(sel).first
            try:
                if await loc.count() > 0:
                    txt = (await loc.inner_text()).strip()
                    if txt:
                        return txt
            except Exception:  # noqa: BLE001
                continue
        return ""

    async def _train_label_of(self, card: Locator) -> str:
        try:
            text = (await card.inner_text()) or ""
            return text.strip().splitlines()[0][:60]
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ #
    # Low-level locator helpers
    # ------------------------------------------------------------------ #

    async def _first(self, candidates: list[str], *, what: str = "",
                     timeout: float = 4_000, required: bool = False) -> Locator | None:
        assert self.page is not None
        for sel in candidates:
            loc = self.page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=timeout / len(candidates))
                return loc
            except PWTimeout:
                continue
            except Exception:  # noqa: BLE001
                continue
        if required:
            raise RuntimeError(f"None of the selectors matched for {what or candidates!r}")
        return None

    async def _fill_first(self, candidates: list[str], value: str, *, what: str = "",
                          required: bool = True) -> None:
        loc = await self._first(candidates, what=what, required=required)
        if loc is None:
            return
        await loc.click()
        await loc.fill(value)

    async def _fill_nth(self, candidates: list[str], index: int, value: str, *, what: str = "",
                        required: bool = True) -> None:
        assert self.page is not None
        loc = self.page.locator(_join(candidates)).nth(index)
        try:
            await loc.wait_for(state="visible", timeout=5_000)
            await loc.click()
            await loc.fill(value)
        except Exception as exc:  # noqa: BLE001
            if required:
                await self._log(f"Could not fill {what}: {exc}", Level.WARN)

    async def _select_nth(self, candidates: list[str], index: int, value: str, *, what: str = "",
                          required: bool = True) -> None:
        """Select a value on the Nth <select> or PrimeNG dropdown."""
        assert self.page is not None
        loc = self.page.locator(_join(candidates)).nth(index)
        try:
            await loc.wait_for(state="visible", timeout=5_000)
            tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                try:
                    await loc.select_option(label=value)
                except Exception:
                    await loc.select_option(value=value)
            else:
                await loc.click()
                await asyncio.sleep(0.3)
                item = self.page.locator(_join(S.DROPDOWN_ITEMS)).filter(has_text=value).first
                await item.click(timeout=3_000)
        except Exception as exc:  # noqa: BLE001
            if required:
                await self._log(f"Could not set {what} to '{value}': {exc}", Level.WARN)

    async def _click_first(self, candidates: list[str], *, what: str = "",
                           required: bool = True) -> bool:
        loc = await self._first(candidates, what=what, required=required)
        if loc is None:
            return False
        await loc.click()
        return True

    async def _text_of(self, candidates: list[str]) -> str:
        loc = await self._first(candidates, timeout=2_000)
        if loc is None:
            return ""
        try:
            return (await loc.inner_text()) or ""
        except Exception:  # noqa: BLE001
            return ""

    async def _any_visible(self, candidates: list[str], *, timeout: float = 1_000) -> bool:
        loc = await self._first(candidates, timeout=timeout)
        return loc is not None

    async def _is_logged_in(self) -> bool:
        return await self._any_visible(S.LOGGED_IN_MARKERS, timeout=1_500)

    async def _dismiss_popups(self) -> None:
        """IRCTC shows a 'disha' chatbot / alert modal on load — close if present."""
        assert self.page is not None
        for sel in ('button:has-text("OK")', ".ui-dialog-titlebar-close", 'i[aria-label="Close"]'):
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=1_500)
                    await asyncio.sleep(0.3)
            except Exception:  # noqa: BLE001
                continue

    # ------------------------------------------------------------------ #
    # Timing helpers
    # ------------------------------------------------------------------ #

    async def _sleep_interval(self) -> None:
        import random  # local import: keep module import side-effect free

        t = self.config.timing
        base = max(0.0, t.check_interval_seconds)
        jitter = random.uniform(0, max(0.0, t.jitter_seconds)) if t.jitter_seconds else 0.0
        total = base + jitter
        remaining = total
        while remaining > 0:
            self._raise_if_stopping()
            await self._emit(
                BotEvent(
                    kind="countdown",
                    message=f"Next check in {remaining:0.0f}s",
                    phase=Phase.POLLING,
                    data={"remaining": remaining, "reason": "interval"},
                )
            )
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _screenshot(self, name: str) -> None:
        if not self.config.behavior.save_screenshots or self.page is None:
            return
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S")
            path = self.screenshot_dir / f"{stamp}-{name}.png"
            await self.page.screenshot(path=str(path), full_page=False)
        except Exception:  # noqa: BLE001
            pass

    def _raise_if_stopping(self) -> None:
        if self._stop.is_set():
            raise _StopRequested()


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #


class _StopRequested(Exception):
    """Internal signal used to unwind the run loop on stop()."""


def _join(candidates: list[str]) -> str:
    """Join candidate selectors into one Playwright ``,`` (CSS-or) selector.

    Only CSS candidates can be OR-joined; ``text=``/``xpath=`` entries are dropped
    for the joined form (they're still tried individually by ``_first``).
    """
    css = [c for c in candidates if not c.startswith(("text=", "xpath=", "//"))]
    return ", ".join(css) if css else candidates[0]


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _class_label(code: str) -> str:
    """The class dropdown lists full labels; map a code to something searchable."""
    mapping = {
        "SL": "Sleeper", "3A": "AC 3 Tier", "3E": "3 Tier", "2A": "AC 2 Tier",
        "1A": "First AC", "CC": "Chair Car", "EC": "Exec", "2S": "Second Sitting",
        "FC": "First Class",
    }
    return mapping.get(code.upper(), code)


def _label_is_before(shown: str, target: str) -> bool:
    """True if the ``shown`` 'month year' label is earlier than ``target``."""
    def key(label: str) -> tuple[int, int]:
        try:
            m, y = label.split()
            return (int(y), _MONTHS.index(m))
        except Exception:  # noqa: BLE001
            return (0, 0)
    return key(shown) < key(target)


def _loop_time() -> float:
    return asyncio.get_event_loop().time()

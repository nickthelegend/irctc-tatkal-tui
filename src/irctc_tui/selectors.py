"""Centralised IRCTC selectors and availability parsing.

IRCTC's booking site is an Angular app (the ``/nget/`` bundle) built on PrimeNG
widgets. Its DOM changes every few releases, so **every selector the tool relies
on lives here** — when something breaks, this is the only file you should need to
touch.

Each UI element is a *list of candidate selectors* tried in order until one
matches (see ``automation._first``). Candidates use Playwright selector syntax:

* plain CSS (``input[formcontrolname="userName"]``)
* ``text=...`` engine (``text=LOGIN``)
* XPath (``xpath=//a[normalize-space()='LOGIN']``)

Put the most specific/stable candidate first and looser fallbacks after.

To check which candidates still match the live site (and discover new control
ids/formcontrolnames when IRCTC changes its DOM), run::

    python -m irctc_tui.recon           # prints a ✓/✗ report per group
"""

from __future__ import annotations

from enum import Enum

# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #

# The public link redirects here; navigating to either works.
SEARCH_URL = "https://www.irctc.co.in/nget/train-search"
LEGACY_SEARCH_URL = "https://www.irctc.co.in/eticket/train-search"
BOOKING_URL_FRAGMENT = "/nget/booking/"  # passenger + review pages live under here


# --------------------------------------------------------------------------- #
# Login modal
# --------------------------------------------------------------------------- #

LOGIN_NAV_BUTTON = [
    "a.loginText",
    "a.search_btn.loginText",
    "text=LOGIN",
    "xpath=//a[normalize-space()='LOGIN']",
    "xpath=//a[contains(@class,'login')]",
]

USERNAME_INPUT = [
    'input[formcontrolname="userName"]',
    "#userId",
    'input[placeholder="User Name"]',
    'input[aria-label="User Name"]',
]

PASSWORD_INPUT = [
    'input[formcontrolname="password"]',
    "#pwd",
    'input[placeholder="Password"]',
    'input[type="password"]',
]

LOGIN_CAPTCHA_IMAGE = [
    "img.captcha-img",
    ".captcha-img",
    'img[src*="captcha"]',
]

LOGIN_CAPTCHA_INPUT = [
    'input[formcontrolname="captcha"]',
    "#captcha",
    'input[placeholder="Enter Captcha"]',
]

SIGN_IN_BUTTON = [
    'button:has-text("SIGN IN")',
    "xpath=//button[normalize-space()='SIGN IN']",
    'button[type="submit"].train_Search',
]

# Presence of any of these means we are logged in.
LOGGED_IN_MARKERS = [
    'a:has-text("LOGOUT")',
    "text=LOGOUT",
    ".usericon-color",
    "xpath=//a[contains(text(),'Logout')]",
]


# --------------------------------------------------------------------------- #
# Search form
# --------------------------------------------------------------------------- #

FROM_STATION_INPUT = [
    "#origin input",
    'p-autocomplete[formcontrolname="origin"] input',
    "#origin",
    'input[aria-controls="pr_id_1_list"]',
    'input[placeholder="From*"]',
]

TO_STATION_INPUT = [
    "#destination input",
    'p-autocomplete[formcontrolname="destination"] input',
    "#destination",
    'input[aria-controls="pr_id_2_list"]',
    'input[placeholder="To*"]',
]

# Suggestion items that drop down under either autocomplete.
AUTOCOMPLETE_ITEMS = [
    ".ui-autocomplete-panel li",
    "ul.ui-autocomplete-items li",
    ".p-autocomplete-item",
    "p-autocomplete .ui-autocomplete-list-item",
]

JOURNEY_DATE_INPUT = [
    'p-calendar[formcontrolname="journeyDate"] input',
    "#jDate",
    'input[placeholder="DD-MM-YYYY"]',
    'input[aria-label="Journey Date(dd-mm-yyyy)"]',
]

# PrimeNG calendar (journey date picker) — used when the field can't be typed.
CALENDAR_PANEL = [".ui-datepicker", ".p-datepicker"]
CALENDAR_PREV = [".ui-datepicker-prev", ".p-datepicker-prev", 'a[title="Prev"]']
CALENDAR_NEXT = [".ui-datepicker-next", ".p-datepicker-next", 'a[title="Next"]']
CALENDAR_MONTH_LABEL = [".ui-datepicker-month", ".p-datepicker-month"]
CALENDAR_YEAR_LABEL = [".ui-datepicker-year", ".p-datepicker-year"]
CALENDAR_DAY_CELLS = [
    ".ui-datepicker-calendar td a",
    ".p-datepicker-calendar td span:not(.p-disabled)",
    "table.ui-datepicker-calendar td a",
]

QUOTA_DROPDOWN = [
    "#journeyQuota",
    'p-dropdown[formcontrolname="journeyQuota"]',
    'p-dropdown[id="journeyQuota"]',
]

CLASS_DROPDOWN = [
    "#jClass",
    'p-dropdown[formcontrolname="journeyClass"]',
]

# Dropdown option items (used for both quota and class panels).
DROPDOWN_ITEMS = [
    ".ui-dropdown-panel li",
    ".ui-dropdown-items li",
    ".p-dropdown-item",
    'li[role="option"]',
]

SEARCH_BUTTON = [
    'button:has-text("Search")',
    "button.search_btn.train_Search",
    "xpath=//button[normalize-space()='Search']",
]


# --------------------------------------------------------------------------- #
# Results / availability
# --------------------------------------------------------------------------- #

# One card per train in the results list.
TRAIN_CARD = [
    "app-train-avl-enq",
    ".train-list-item",
    ".bull-back",
]

# The clickable class boxes (SL, 3A, …) inside a train card.
CLASS_CELL = [
    ".pre-avl",
    "td.link",
    ".class-type",
    ".ng-star-inserted .link",
]

# --- Results parsing (verified against the live IRCTC DOM, SC→TPTY 24-Jul-2026) ---
# Train name + number, e.g. "KRISHNA EXPRESS (17406)".
TRAIN_HEADING = [".train-heading strong", ".train-heading", "app-train-avl-enq strong"]
# Departure / arrival time nodes (NOT the duration in .line-hr). ".time" matches
# both the span.time (departure) and strong.time (arrival), incl. mobile copies.
TRAIN_TIME = [".time"]
# The currently selected (clicked) class tab, e.g. "Sleeper (SL)".
ACTIVE_CLASS_TAB = [
    ".ui-state-active .hidden-xs",
    "p-tabmenu .ui-state-active .ui-menuitem-text",
    ".ui-state-active .ui-menuitem-text",
]
# Every class tab a train offers, e.g. "Sleeper (SL)", "AC 3 Tier (3A)".
ALL_CLASS_TABS = [".ui-tabmenuitem .hidden-xs", "p-tabmenu li .ui-menuitem-text"]
# Date-wise availability cells: each holds a date <strong> and a status div
# whose CLASS is WL / RAC / AVAILABLE / REGRET and whose text is e.g. "WL30".
AVAIL_DATE_CELL = ["td.link .pre-avl", "table .pre-avl", ".pre-avl"]

# Legacy alias kept for any external references.
RESULT_CLASS_CELL = AVAIL_DATE_CELL

# Availability text nodes inside a class' date column.
AVAILABILITY_STATUS = [
    ".AVAILABLE",
    ".WL",
    ".RAC",
    ".REGRET",
    "div.pre-avl strong",
    ".avl-txt",
]

REFRESH_AVAILABILITY = [
    ".refresh",
    'a[title="Refresh"]',
    "text=Refresh",
]

BOOK_NOW_BUTTON = [
    'button:has-text("Book Now")',
    "xpath=//button[normalize-space()='Book Now']",
    "button.btnDefault.train_Search",
]


# --------------------------------------------------------------------------- #
# Passenger input page
# --------------------------------------------------------------------------- #

PASSENGER_NAME_INPUT = [
    'p-autocomplete[formcontrolname="passengerName"] input',
    'input[formcontrolname="passengerName"]',
    'input[placeholder="Name"]',
]

PASSENGER_AGE_INPUT = [
    'input[formcontrolname="passengerAge"]',
    'input[placeholder="Age"]',
]

PASSENGER_GENDER_SELECT = [
    'select[formcontrolname="passengerGender"]',
    'p-dropdown[formcontrolname="passengerGender"]',
]

PASSENGER_BERTH_SELECT = [
    'select[formcontrolname="passengerBerthChoice"]',
    'p-dropdown[formcontrolname="passengerBerthChoice"]',
]

PASSENGER_FOOD_SELECT = [
    'select[formcontrolname="passengerFoodChoice"]',
    'p-dropdown[formcontrolname="passengerFoodChoice"]',
]

PASSENGER_NATIONALITY_SELECT = [
    'select[formcontrolname="passengerNationality"]',
    'p-dropdown[formcontrolname="passengerNationality"]',
]

ADD_PASSENGER_LINK = [
    'a:has-text("Add Passenger")',
    "text=+ Add Passenger",
    "xpath=//a[contains(text(),'Add Passenger')]",
]

MOBILE_INPUT = [
    'input[formcontrolname="mobileNumber"]',
    'input[placeholder="Mobile Number"]',
    "#mobileNumber",
]

# "Book only if confirmed berths are allotted" checkbox.
CONFIRM_BERTHS_CHECKBOX = [
    "#confirmberths",
    'p-checkbox[formcontrolname="confirmberths"]',
]

# Auto-upgrade checkbox.
AUTO_UPGRADE_CHECKBOX = [
    "#autoUpgradation",
    'p-checkbox[formcontrolname="autoUpgradationSelected"]',
]

PASSENGER_CONTINUE_BUTTON = [
    "#nextButton",
    'button:has-text("Continue")',
    "#psgn-form button.train_Search",
    "xpath=//button[normalize-space()='Continue']",
]


# --------------------------------------------------------------------------- #
# Review / payment (the tool STOPS before touching these — human only)
# --------------------------------------------------------------------------- #

REVIEW_CAPTCHA_INPUT = [
    'input[formcontrolname="captcha"]',
    "#captcha",
    'input[placeholder="Enter Captcha"]',
]

REVIEW_CONTINUE_BUTTON = [
    'button:has-text("Continue")',
    "xpath=//button[normalize-space()='Continue']",
]

# Any of these on screen means we've reached the point where the human takes over.
PAYMENT_PAGE_MARKERS = [
    "text=Payment Methods",
    "text=BHIM/UPI",
    "text=Pay & Book",
    ".payment-options",
]


# --------------------------------------------------------------------------- #
# Availability classification
# --------------------------------------------------------------------------- #


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    RAC = "RAC"
    WAITLIST = "WAITLIST"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNKNOWN = "UNKNOWN"

    @property
    def bookable(self) -> bool:
        # RAC is bookable (you get a seat, possibly shared) — treat it as such.
        # WAITLIST under Tatkal is not allowed anyway, so it's not bookable.
        return self in (Availability.AVAILABLE, Availability.RAC)


def classify_availability(text: str) -> Availability:
    """Map raw IRCTC availability text to an :class:`Availability`.

    Examples of raw text: ``"AVAILABLE-0021"``, ``"CURR_AVBL-0005"``,
    ``"RAC 12"``, ``"GNWL 34/WL 21"``, ``"REGRET/WL"``, ``"NOT AVAILABLE"``,
    ``"TRAIN DEPARTED"``, ``"CHART PREPARED"``.
    """
    t = (text or "").strip().upper()
    if not t:
        return Availability.UNKNOWN

    hard_no = ("NOT AVAILABLE", "DEPARTED", "CHART PREPARED", "CANCELLED", "NOT AVBL")
    if any(k in t for k in hard_no):
        return Availability.NOT_AVAILABLE

    if "RAC" in t:
        return Availability.RAC

    # "REGRET" or any waitlist marker (WL/GNWL/RLWL/PQWL/RSWL) => waitlist.
    if "REGRET" in t or "WL" in t:
        return Availability.WAITLIST

    if "AVAILABLE" in t or "AVBL" in t or "CURR_AVBL" in t:
        return Availability.AVAILABLE

    return Availability.UNKNOWN

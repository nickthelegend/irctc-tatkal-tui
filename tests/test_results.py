"""Results parser — pure functions plus an integration test against REAL captured
IRCTC HTML (``fixtures/irctc_results_real.html``, SC→Tirupati 24-Jul-2026).

The fixture is not a hand-written mock: it is the actual ``<app-train-avl-enq>``
markup captured from a live IRCTC search (Angular attributes stripped).
"""

import asyncio
from pathlib import Path

import pytest

from irctc_tui.results import (
    Train,
    code_from_label,
    date_token,
    extract_fare,
    extract_status,
    parse_results,
    parse_train_header,
    pick_target,
    status_for_date,
)
from irctc_tui.selectors import Availability

REAL_FIXTURE = (Path(__file__).parent / "fixtures" / "irctc_results_real.html").as_uri()


# ---- pure parsing --------------------------------------------------------- #


def test_parse_train_header_name_and_number():
    t = parse_train_header("KRISHNA EXPRESS (17406)\nRuns On: MTWTFSS\n05:55 | CHARLAPALLI")
    assert t.name == "KRISHNA EXPRESS"
    assert t.number == "17406"


def test_code_from_label():
    assert code_from_label("Sleeper (SL)") == "SL"
    assert code_from_label("AC 3 Tier (3A)") == "3A"
    assert code_from_label("Exec. Chair Car (EC)") == "EC"
    assert code_from_label("nonsense") == ""


def test_date_token():
    assert date_token("24-07-2026") == "24 Jul"
    assert date_token("04-08-2026") == "04 Aug"
    assert date_token("bad") == ""


@pytest.mark.parametrize(
    "text,status",
    [
        ("Fri, 24 Jul WL30", "WL30"),
        ("Fri, 24 Jul REGRET", "REGRET"),
        ("Tue, 04 Aug RAC 33", "RAC 33"),
        ("Fri, 24 Jul AVAILABLE-0021", "AVAILABLE-0021"),
        ("Fri, 24 Jul", ""),
    ],
)
def test_extract_status(text, status):
    assert extract_status(text) == status


def test_extract_fare():
    assert extract_fare("Book Now ₹ 415 info") == "₹415"
    assert extract_fare("no fare here") == ""


def test_status_for_date_prefers_journey_date():
    cells = ["Fri, 24 Jul WL30", "Sat, 25 Jul WL31", "Sun, 26 Jul WL16"]
    assert status_for_date(cells, "24-07-2026") == "WL30"
    assert status_for_date(cells, "25-07-2026") == "WL31"
    # unknown date → first with a status
    assert status_for_date(cells, "01-01-2030") == "WL30"


def test_pick_target_prefers_first_bookable():
    from irctc_tui.results import ClassAvailability

    trains = [
        Train("17434", "RXL TPTY", classes=[ClassAvailability("SL", "REGRET", Availability.WAITLIST)]),
        Train("12734", "NARAYANADRI", classes=[ClassAvailability("SL", "AVAILABLE-0021", Availability.AVAILABLE)]),
    ]
    train, ca = pick_target(trains, "SL")
    assert train.number == "12734" and ca.bookable


# ---- integration against the REAL captured IRCTC HTML --------------------- #


def test_parse_results_against_real_irctc_capture():
    async def scenario():
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"Playwright browser unavailable: {exc}")
            page = await (await browser.new_context()).new_page()
            await page.goto(REAL_FIXTURE)
            trains = await parse_results(page, journey_date="24-07-2026", want_class="SL")
            await browser.close()
            return trains

    trains = asyncio.run(scenario())
    by_num = {t.number: t for t in trains}
    assert {"17406", "17434"} <= set(by_num)

    krishna = by_num["17406"]
    assert krishna.name == "KRISHNA EXPRESS"
    assert krishna.departure == "05:55" and krishna.arrival == "21:40"
    sl = krishna.availability_for("SL")
    assert sl.status_raw == "WL30"
    assert sl.availability is Availability.WAITLIST
    assert sl.fare == "₹415"
    assert krishna.availability_for("3A") is not None  # offered class detected

    rxl = by_num["17434"]
    assert rxl.availability_for("SL").status_raw == "REGRET"
    assert rxl.availability_for("SL").availability is Availability.WAITLIST

    # Nothing is bookable for SL on 24-Jul under General quota (all WL/REGRET) —
    # exactly why Tatkal is needed. pick_target still reports the first train.
    train, ca = pick_target(trains, "SL")
    assert not ca.bookable
    assert train.number in {"17406", "17434"}

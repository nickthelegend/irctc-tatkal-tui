"""Results parser — pure functions (fast) and a browser integration test."""

import asyncio
from pathlib import Path

import pytest

from irctc_tui.results import (
    parse_class_cell,
    parse_results,
    parse_train_header,
    pick_target,
)
from irctc_tui.selectors import Availability

FIXTURE = (Path(__file__).parent / "fixtures" / "irctc_results_mock.html").as_uri()


# ---- pure parsing --------------------------------------------------------- #


def test_parse_train_header_name_number_times():
    t = parse_train_header("NARAYANADRI EXPRESS (12734)\nDep 20:00 · SC → TPTY · Arr 06:30")
    assert t.name == "NARAYANADRI EXPRESS"
    assert t.number == "12734"
    assert t.departure == "20:00"
    assert t.arrival == "06:30"


@pytest.mark.parametrize(
    "text,code,avail,fare",
    [
        ("SL ₹385 AVAILABLE-0021", "SL", Availability.AVAILABLE, "₹385"),
        ("3A ₹1,010 RAC 5", "3A", Availability.RAC, "₹1,010"),
        ("2A ₹1450 GNWL 34/WL 21", "2A", Availability.WAITLIST, "₹1450"),
        ("2S ₹120 NOT AVAILABLE", "2S", Availability.NOT_AVAILABLE, "₹120"),
        ("CC ₹500 CURR_AVBL-0009", "CC", Availability.AVAILABLE, "₹500"),
    ],
)
def test_parse_class_cell(text, code, avail, fare):
    ca = parse_class_cell(text)
    assert ca is not None
    assert ca.class_code == code
    assert ca.availability is avail
    assert ca.fare == fare


def test_parse_class_cell_none_when_no_class():
    assert parse_class_cell("₹385 some noise") is None
    assert parse_class_cell("") is None


def test_pick_target_prefers_first_bookable():
    from irctc_tui.results import Train

    trains = [
        Train("12764", "PADMAVATI", classes=[parse_class_cell("SL WL 40")]),
        Train("12734", "NARAYANADRI", classes=[parse_class_cell("SL AVAILABLE-0021")]),
    ]
    train, ca = pick_target(trains, "SL")
    assert train.number == "12734"
    assert ca.bookable


def test_pick_target_honours_train_number():
    from irctc_tui.results import Train

    trains = [
        Train("12734", "NARAYANADRI", classes=[parse_class_cell("SL AVAILABLE-0021")]),
        Train("12764", "PADMAVATI", classes=[parse_class_cell("SL WL 40")]),
    ]
    train, ca = pick_target(trains, "SL", train_number="12764")
    assert train.number == "12764"  # requested train wins even though it's WL


# ---- DOM integration ------------------------------------------------------ #


def test_parse_results_against_fixture():
    async def scenario():
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"Playwright browser unavailable: {exc}")
            page = await (await browser.new_context()).new_page()
            await page.goto(FIXTURE)
            trains = await parse_results(page)
            await browser.close()
            return trains

    trains = asyncio.run(scenario())
    assert len(trains) == 3

    by_num = {t.number: t for t in trains}
    assert set(by_num) == {"12734", "12764", "17209"}

    narayanadri = by_num["12734"]
    assert narayanadri.name == "NARAYANADRI EXPRESS"
    assert narayanadri.departure == "20:00"
    assert narayanadri.availability_for("SL").availability is Availability.AVAILABLE
    assert narayanadri.availability_for("3A").availability is Availability.RAC
    assert narayanadri.availability_for("2A").availability is Availability.WAITLIST

    # first bookable SL across the results is 12734
    train, ca = pick_target(trains, "SL")
    assert train.number == "12734" and ca.bookable

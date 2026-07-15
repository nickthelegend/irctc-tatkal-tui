"""irctc-search: arg parsing, table formatting, and a real-HTML integration run."""

import asyncio
from pathlib import Path

import pytest

from irctc_tui.config import AppConfig, BehaviorConfig, JourneyConfig
from irctc_tui.results import ClassAvailability, Train
from irctc_tui.search_cli import build_parser, format_table, run_search
from irctc_tui.selectors import Availability

REAL_FIXTURE = (Path(__file__).parent / "fixtures" / "irctc_results_real.html").as_uri()


def test_arg_defaults():
    args = build_parser().parse_args([])
    assert args.from_station == "SC" and args.to_station == "TPTY"
    assert args.travel_class == "SL" and args.quota == "GENERAL"


def test_format_table_marks_bookable():
    trains = [
        Train("12734", "NARAYANADRI EXPRESS", "20:00", "06:30",
              classes=[ClassAvailability("SL", "AVAILABLE-0021", Availability.AVAILABLE, "₹385")]),
        Train("17434", "RXL TPTY EXP", "19:05", "09:30",
              classes=[ClassAvailability("SL", "REGRET", Availability.WAITLIST, "₹415")]),
    ]
    out = format_table(trains, "SL")
    assert "NARAYANADRI EXPRESS" in out and "12734" in out
    assert "AVAILABLE-0021" in out and "REGRET" in out
    assert "✓" in out  # the AVAILABLE train is marked bookable


def test_run_search_parses_real_capture():
    config = AppConfig(
        journey=JourneyConfig(from_station="SC", to_station="TPTY",
                              journey_date="24-07-2026", travel_class="SL", quota="GENERAL"),
        behavior=BehaviorConfig(headed=False, save_screenshots=False),
    )

    async def scenario():
        try:
            return await run_search(config, results_url=REAL_FIXTURE)
        except Exception as exc:  # noqa: BLE001
            if "Executable doesn't exist" in str(exc) or "BrowserType.launch" in str(exc):
                pytest.skip(f"Playwright browser unavailable: {exc}")
            raise

    trains = asyncio.run(scenario())
    by_num = {t.number: t for t in trains}
    assert {"17406", "17434"} <= set(by_num)
    assert by_num["17406"].availability_for("SL").status_raw == "WL30"
    assert by_num["17434"].availability_for("SL").status_raw == "REGRET"

"""Integration test: the real IRCTCBot engine fills the search form.

Runs the tool's actual Playwright automation (`_fill_and_submit_search`) against a
local replica of IRCTC's PrimeNG form (``fixtures/irctc_search_mock.html``) and
asserts the engine selected the right origin/destination/date/quota/class and
clicked Search. No network or live IRCTC needed — this proves the *engine* works;
the live run happens on the user's machine.

Skipped automatically if a Playwright browser isn't installed.
"""

import asyncio
from pathlib import Path

import pytest

from irctc_tui.automation import IRCTCBot
from irctc_tui.config import AppConfig, BehaviorConfig, JourneyConfig

FIXTURE = (Path(__file__).parent / "fixtures" / "irctc_search_mock.html").as_uri()


def test_engine_fills_search_form_against_local_replica():
    config = AppConfig(
        journey=JourneyConfig(
            from_station="SC", to_station="TPTY", journey_date="24-07-2026",
            travel_class="SL", quota="TATKAL",
        ),
        behavior=BehaviorConfig(headed=False, save_screenshots=False),
    )

    async def scenario():
        bot = IRCTCBot(config)
        try:
            await bot._launch()
        except Exception as exc:  # noqa: BLE001 - no browser in this environment
            pytest.skip(f"Playwright browser unavailable: {exc}")
        try:
            await bot.page.goto(FIXTURE)
            await bot._fill_and_submit_search()
            await bot.page.wait_for_timeout(300)
            return await bot.page.evaluate("() => window.__captured")
        finally:
            await bot.close()

    captured = asyncio.run(scenario())

    assert "SC" in captured["origin"]           # typed 'SC' → picked Secunderabad Jn
    assert "TPTY" in captured["destination"]     # picked Tirupati
    assert captured["date"] == "24-07-2026"      # navigated the calendar to the 24th
    assert captured["quota"] == "TATKAL"         # opened the quota dropdown, chose Tatkal
    assert "SL" in captured["travelClass"]       # 'SL' → 'Sleeper' label match
    assert captured["searched"] is True          # clicked Search

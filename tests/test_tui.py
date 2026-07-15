"""Headless smoke test of the Textual app via its test pilot.

These never launch a browser — they only drive the UI and inspect state, so they
are safe and fast in CI.
"""

import asyncio

from irctc_tui.app import IRCTCApp
from textual.widgets import DataTable, Input, TabbedContent


def _run(coro):
    asyncio.run(coro)


def test_app_mounts_with_example_config(tmp_path):
    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test():
            # example config seeds one passenger
            table = app.query_one("#passenger_table", DataTable)
            assert table.row_count == 1
            cfg = app._collect_config()
            assert cfg.journey.from_station == "SC"
            assert cfg.journey.quota == "TATKAL"

    _run(scenario())


def test_add_and_remove_passenger(tmp_path):
    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test() as pilot:
            table = app.query_one("#passenger_table", DataTable)
            start = table.row_count
            app.query_one(TabbedContent).active = "tab-passengers"
            await pilot.pause()
            app.query_one("#p_name", Input).value = "Smoke Test"
            app.query_one("#p_age", Input).value = "33"
            await pilot.click("#add_passenger")
            assert table.row_count == start + 1
            assert any(p.name == "Smoke Test" for p in app._collect_config().passengers)

            # Now remove it again via the row cursor.
            app.query_one("#passenger_table", DataTable).move_cursor(row=start)
            await pilot.click("#remove_passenger")
            assert table.row_count == start

    _run(scenario())


def test_start_blocks_on_invalid_config(tmp_path):
    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test():
            # Break the config: identical stations.
            app.query_one("#to_station", Input).value = app.query_one("#from_station", Input).value
            app.action_validate()
            problems = app._collect_config().validate()
            assert any("same" in p for p in problems)
            # Starting must not spawn a worker while config is invalid.
            app.action_start()
            assert app._worker is None

    _run(scenario())


def test_save_writes_config_file(tmp_path):
    async def scenario():
        path = tmp_path / "config.json"
        app = IRCTCApp(config_path=path)
        async with app.run_test():
            app.query_one("#from_station", Input).value = "KCG"
            app.action_save()
            assert path.exists()
            from irctc_tui.config import AppConfig

            assert AppConfig.load(path).journey.from_station == "KCG"

    _run(scenario())

"""Headless smoke test of the Textual app via its test pilot.

These never launch a browser — they only drive the UI and inspect state, so they
are safe and fast in CI.
"""

import asyncio

from textual.widgets import DataTable, Input, Select, TabbedContent

from irctc_tui.app import IRCTCApp


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
            app.query_one("#to_station", Select).value = app.query_one("#from_station", Select).value
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
            app.query_one("#from_station", Select).value = "KCG"
            app.action_save()
            assert path.exists()
            from irctc_tui.config import AppConfig

            assert AppConfig.load(path).journey.from_station == "KCG"

    _run(scenario())


def test_alarm_settings_collected_and_silence_is_safe(tmp_path):
    from textual.widgets import Switch

    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test():
            app.query_one("#alarm_on_success", Switch).value = False
            app.query_one("#alarm_sound_path", Input).value = "/tmp/song.mp3"
            cfg = app._collect_config()
            assert cfg.behavior.alarm_on_success is False
            assert cfg.behavior.alarm_sound_path == "/tmp/song.mp3"
            # Silencing when nothing rings must not raise or start audio.
            app.action_silence()
            assert app._alarm is None

    _run(scenario())


def test_telegram_settings_collected_and_validated(tmp_path):
    from textual.widgets import Switch

    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test() as pilot:
            app.query_one(TabbedContent).active = "tab-telegram"
            await pilot.pause()
            app.query_one("#tg_enabled", Switch).value = True
            # enabled but blank creds -> validation complains
            problems = app._collect_config().validate()
            assert any("bot token is empty" in p for p in problems)
            assert any("owner id is empty" in p for p in problems)
            # fill them in
            app.query_one("#tg_token", Input).value = "123:abc"
            app.query_one("#tg_owner", Input).value = "555"
            cfg = app._collect_config()
            assert cfg.telegram.enabled is True
            assert cfg.telegram.bot_token == "123:abc"
            assert cfg.telegram.owner_id == "555"

    _run(scenario())


def test_preflight_button_present_but_not_triggered(tmp_path):
    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test():
            # Button exists; we intentionally do NOT press it (it launches a browser).
            assert app.query_one("#preflight") is not None
            assert app._preflight_worker is None

    _run(scenario())


def test_from_to_are_dropdowns_with_station_values(tmp_path):
    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test():
            frm = app.query_one("#from_station", Select)
            to = app.query_one("#to_station", Select)
            assert frm.value == "SC" and to.value == "TPTY"
            frm.value = "KCG"  # a value from the dropdown list
            assert app._collect_config().journey.from_station == "KCG"

    _run(scenario())


def test_results_table_populates_from_event(tmp_path):
    from irctc_tui.events import BotEvent

    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test():
            trains = [
                {"name": "NARAYANADRI EXPRESS", "number": "12734", "departure": "20:00",
                 "arrival": "06:30", "classes": [
                     {"class_code": "SL", "status_raw": "AVAILABLE-0021",
                      "availability": "AVAILABLE", "fare": "₹385", "bookable": True}]},
                {"name": "PADMAVATI EXPRESS", "number": "12764", "departure": "18:25",
                 "arrival": "05:10", "classes": [
                     {"class_code": "SL", "status_raw": "WL 45",
                      "availability": "WAITLIST", "fare": "₹385", "bookable": False}]},
            ]
            await app._on_bot_event(BotEvent(kind="results",
                                             data={"trains": trains, "target_class": "SL"}))
            table = app.query_one("#results_table", DataTable)
            assert table.row_count == 2

    _run(scenario())


def test_telegram_commands_dispatch_safely(tmp_path):
    async def scenario():
        app = IRCTCApp(config_path=tmp_path / "config.json")
        async with app.run_test():
            # 'run' would launch a browser, so we never send it here.
            assert "status" in (await app._handle_tg_command("status")).lower()
            assert "remote commands" in (await app._handle_tg_command("help")).lower()
            assert "remote commands" in (await app._handle_tg_command("/start")).lower()
            assert "silenced" in (await app._handle_tg_command("silence")).lower()
            assert "stop requested" in (await app._handle_tg_command("stop")).lower()
            assert "no browser" in (await app._handle_tg_command("shot")).lower()
            assert "unknown" in (await app._handle_tg_command("frobnicate")).lower()

    _run(scenario())

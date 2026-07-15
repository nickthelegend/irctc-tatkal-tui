"""CDP-attach: the CLI drives an EXTERNAL browser (started by us) over the
DevTools protocol, then parses the real captured IRCTC results through it.

This is the same mechanism that, on your machine, attaches the tool to your real
logged-in Chrome (started with --remote-debugging-port) so it uses that browser's
network + IRCTC session.
"""

import asyncio
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from irctc_tui.config import AppConfig, BehaviorConfig, JourneyConfig
from irctc_tui.search_cli import build_parser, run_search

REAL_FIXTURE = (Path(__file__).parent / "fixtures" / "irctc_results_real.html").as_uri()


def test_cdp_flag_flows_to_config():
    args = build_parser().parse_args(["--cdp", "http://127.0.0.1:9222"])
    assert args.cdp == "http://127.0.0.1:9222"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _chromium_executable() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        return p.chromium.executable_path


def test_run_search_attaches_over_cdp(tmp_path):
    try:
        exe = _chromium_executable()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright chromium unavailable: {exc}")

    port = _free_port()
    proc = subprocess.Popen(
        [exe, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={tmp_path}", "--no-first-run", "--no-default-browser-check"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for the external browser's CDP endpoint to come up.
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            pytest.skip("CDP endpoint did not come up")

        config = AppConfig(
            journey=JourneyConfig(from_station="SC", to_station="TPTY",
                                  journey_date="24-07-2026", travel_class="SL"),
            behavior=BehaviorConfig(cdp_url=f"http://127.0.0.1:{port}"),
        )
        trains = asyncio.run(run_search(config, results_url=REAL_FIXTURE))
        by_num = {t.number: t for t in trains}
        assert by_num["17406"].availability_for("SL").status_raw == "WL30"
        assert by_num["17434"].availability_for("SL").status_raw == "REGRET"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

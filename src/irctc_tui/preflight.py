"""Pre-flight check: verify selectors against the live IRCTC site from the TUI.

Reuses the recon verification logic but, instead of printing, streams results as
:class:`~irctc_tui.events.BotEvent` log lines to a sink (the TUI's event handler),
so you can confirm everything is wired the morning of your booking without
leaving the app.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from playwright.async_api import async_playwright

from . import selectors as S
from .config import AppConfig
from .events import BotEvent, Level, Phase
from .recon import _UA, verify_selectors

EventSink = Callable[[BotEvent], Awaitable[None] | None]


async def _emit(sink: EventSink | None, message: str, level: Level = Level.INFO,
                *, kind: str = "log", phase: Phase | None = None, data: dict | None = None) -> None:
    if sink is None:
        return
    result = sink(BotEvent(kind=kind, message=message, level=level, phase=phase, data=data or {}))
    if inspect.isawaitable(result):
        await result


async def run_preflight(
    config: AppConfig,
    on_event: EventSink | None = None,
    *,
    url: str | None = None,
    timeout_ms: int = 45_000,
) -> tuple[int, int]:
    """Open the search page and verify every selector group.

    Returns ``(matched_groups, total_groups)``. Emits a ✓/✗ line per group plus a
    phase summary. Never raises for a page-load failure — it reports it instead.
    """
    b = config.behavior
    await _emit(on_event, "Pre-flight: launching browser for a live selector check…",
                kind="phase", phase=Phase.PREFLIGHT)
    async with async_playwright() as pw:
        launcher = getattr(pw, b.browser, pw.chromium)
        browser = await launcher.launch(
            headless=not b.headed,
            args=["--disable-http2", "--disable-blink-features=AutomationControlled",
                  "--start-maximized"],
        )
        ctx = await browser.new_context(user_agent=_UA, no_viewport=b.headed,
                                        ignore_https_errors=True)
        page = await ctx.new_page()
        target = url or S.SEARCH_URL
        await _emit(on_event, f"Opening {target}")
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            await _emit(on_event, f"Pre-flight could not load the page: {exc}",
                        Level.ERROR, kind="phase", phase=Phase.PREFLIGHT)
            await _emit(on_event, "Is this machine able to reach IRCTC? (proxies/VPNs can block it.)",
                        Level.WARN)
            await browser.close()
            return 0, 0

        for sel in ("p-autocomplete", "[formcontrolname=origin]", "input#origin", "input"):
            try:
                await page.wait_for_selector(sel, timeout=8_000)
                break
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(2_000)

        results = await verify_selectors(page)
        matched = 0
        for name, hits, any_hit in results:
            if any_hit:
                matched += 1
                best = next((f"{sel} [{c}]" for sel, c in hits if c), "")
                await _emit(on_event, f"✓ {name} → {best}", Level.SUCCESS)
            else:
                await _emit(on_event, f"✗ {name}: no candidate matched — update selectors.py",
                            Level.ERROR)

        total = len(results)
        level = Level.SUCCESS if matched == total else Level.WARN
        await _emit(on_event, f"Pre-flight done: {matched}/{total} selector groups matched.",
                    level, kind="phase", phase=Phase.PREFLIGHT, data={"matched": matched, "total": total})

        if b.headed:
            await page.wait_for_timeout(1_500)
        await browser.close()
        return matched, total

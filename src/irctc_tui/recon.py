"""Live DOM recon + selector verifier for IRCTC.

Run this on a machine that can actually reach IRCTC. It opens the real search
page and:

1. **Discovers** every form control (inputs, ``p-autocomplete``, ``p-dropdown``,
   ``p-calendar``, buttons) with its ``id`` / ``formcontrolname`` / ``placeholder``.
2. **Verifies** the candidate selectors in :mod:`~irctc_tui.selectors` against the
   live page, printing a ✓/✗ report so you know exactly which ones still match.
3. Optionally writes the raw discovery to JSON (``--json out.json``).

Usage::

    python -m irctc_tui.recon                # headless, verify selectors
    python -m irctc_tui.recon --headed       # watch it in a real window
    python -m irctc_tui.recon --json dom.json # also dump discovered controls

This is how you keep ``selectors.py`` current when IRCTC changes its DOM: run it,
read the ✗ lines, and update the matching selector list.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from playwright.async_api import async_playwright

from . import selectors as S

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Logical field -> the selector list we rely on. Order mirrors the booking flow.
_GROUPS: dict[str, list[str]] = {
    "LOGIN_NAV_BUTTON": S.LOGIN_NAV_BUTTON,
    "FROM_STATION_INPUT": S.FROM_STATION_INPUT,
    "TO_STATION_INPUT": S.TO_STATION_INPUT,
    "JOURNEY_DATE_INPUT": S.JOURNEY_DATE_INPUT,
    "QUOTA_DROPDOWN": S.QUOTA_DROPDOWN,
    "CLASS_DROPDOWN": S.CLASS_DROPDOWN,
    "SEARCH_BUTTON": S.SEARCH_BUTTON,
}

_EXTRACT_JS = r"""
() => {
  const pick = (el, attrs) => {
    const o = {tag: el.tagName.toLowerCase()};
    for (const a of attrs) { const v = el.getAttribute(a); if (v !== null) o[a] = v; }
    if (el.id) o.id = el.id;
    const txt = (el.innerText || '').trim();
    if (txt && txt.length < 40) o.text = txt;
    return o;
  };
  const attrs = ['formcontrolname','placeholder','type','aria-label','role','name','title'];
  return {
    url: location.href,
    title: document.title,
    inputs: [...document.querySelectorAll('input')].map(e => pick(e, attrs)).slice(0, 40),
    selects: [...document.querySelectorAll('select')].map(e => pick(e, attrs)).slice(0, 20),
    pautocomplete: [...document.querySelectorAll('p-autocomplete')].map(e => pick(e, attrs)),
    pdropdown: [...document.querySelectorAll('p-dropdown')].map(e => pick(e, attrs)),
    pcalendar: [...document.querySelectorAll('p-calendar')].map(e => pick(e, attrs)),
    buttons: [...document.querySelectorAll('button')]
        .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 30),
  };
}
"""


async def discover_controls(page) -> dict:
    """Return the raw discovery dict (inputs, p-* widgets, buttons) for ``page``."""
    return await page.evaluate(_EXTRACT_JS)


async def verify_selectors(page) -> list[tuple[str, list[tuple[str, int]], bool]]:
    """For each logical group, test every candidate against ``page``.

    Returns a list of ``(group_name, [(selector, match_count), ...], any_matched)``.
    """
    results: list[tuple[str, list[tuple[str, int]], bool]] = []
    for name, candidates in _GROUPS.items():
        hits: list[tuple[str, int]] = []
        any_hit = False
        for sel in candidates:
            try:
                count = await page.locator(sel).count()
            except Exception:  # noqa: BLE001 - malformed selector shouldn't abort the sweep
                count = 0
            hits.append((sel, count))
            if count:
                any_hit = True
        results.append((name, hits, any_hit))
    return results


async def _run(url: str, headed: bool, timeout_ms: int, json_path: str | None) -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not headed,
            args=["--disable-http2", "--disable-blink-features=AutomationControlled",
                  "--start-maximized"],
        )
        ctx = await browser.new_context(user_agent=_UA, no_viewport=headed,
                                        ignore_https_errors=True)
        page = await ctx.new_page()
        print(f"→ opening {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ Could not load the page: {exc}")
            await browser.close()
            return 2

        # Wait for the Angular form to render.
        for sel in ("p-autocomplete", "[formcontrolname=origin]", "input#origin", "input"):
            try:
                await page.wait_for_selector(sel, timeout=8_000)
                break
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(2_500)

        data = await discover_controls(page)
        _print_discovery(data)
        _print_verification(await verify_selectors(page))

        if json_path:
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            print(f"\n💾 wrote discovered controls to {json_path}")

        if headed:
            print("\n(Leaving the window open 15s so you can look around…)")
            await page.wait_for_timeout(15_000)
        await browser.close()
    return 0


def _print_discovery(data: dict) -> None:
    print(f"\n=== Discovered on {data.get('title', '?')} ===")
    for key in ("pautocomplete", "pdropdown", "pcalendar"):
        items = data.get(key) or []
        if items:
            print(f"\n{key} ({len(items)}):")
            for it in items:
                print("  -", {k: v for k, v in it.items() if k != "tag"})
    inputs = data.get("inputs") or []
    named = [i for i in inputs if i.get("formcontrolname") or i.get("id") or i.get("placeholder")]
    print(f"\ninputs with id/formcontrolname/placeholder ({len(named)}):")
    for it in named[:25]:
        print("  -", {k: v for k, v in it.items() if k != "tag"})
    print("\nbuttons:", ", ".join(data.get("buttons") or []))


def _print_verification(results: list[tuple[str, list[tuple[str, int]], bool]]) -> None:
    print("\n=== Selector verification (✓ = matches live page) ===")
    for name, hits, any_hit in results:
        print(f"\n{name}:")
        for sel, count in hits:
            print(f"  {'✓' if count else '✗'} [{count}] {sel}")
        if not any_hit:
            print("  ⚠ NONE matched — update this group in selectors.py")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="irctc-recon",
        description="Inspect the live IRCTC DOM and verify selectors.py against it.",
    )
    p.add_argument("--url", default=S.SEARCH_URL, help="page to inspect")
    p.add_argument("--headed", action="store_true", help="show a real browser window")
    p.add_argument("--timeout", type=int, default=45_000, help="navigation timeout (ms)")
    p.add_argument("--json", dest="json_path", default=None, help="write discovery JSON here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args.url, args.headed, args.timeout, args.json_path))


if __name__ == "__main__":
    raise SystemExit(main())

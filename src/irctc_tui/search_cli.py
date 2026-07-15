"""``irctc-search`` — a one-shot, no-TUI search + availability parse.

This is the CLI doing the exact same work the TUI does under the hood: it launches
the tool's own browser, navigates to IRCTC, fills the search form, submits it,
parses the results with :mod:`~irctc_tui.results`, and prints a trains table —
then exits. Use it for a quick "what's available" check, or in scripts.

Examples::

    irctc-search --from SC --to TPTY --date 24-07-2026 --class SL --quota GENERAL
    irctc-search --from SC --to TPTY --date 24-07-2026 --class SL --headed

``--results-url`` points the same pipeline at an already-rendered results page
(e.g. a saved copy) and just parses it — handy for testing the parser offline.
"""

from __future__ import annotations

import argparse
import asyncio

from . import selectors as S
from .automation import IRCTCBot
from .config import AppConfig, BehaviorConfig, JourneyConfig
from .results import Train, parse_results, pick_target


async def run_search(
    config: AppConfig,
    *,
    results_url: str | None = None,
    timeout_ms: int = 45_000,
    on_event=None,
) -> list[Train]:
    """Drive the real engine and return the parsed trains.

    If ``results_url`` is given, navigate straight there and parse (skip filling).
    Otherwise do the full search on the live IRCTC site.
    """
    bot = IRCTCBot(config, on_event=on_event)
    await bot._launch()
    try:
        if results_url:
            await bot.page.goto(results_url, timeout=timeout_ms)
        else:
            await bot.page.goto(S.SEARCH_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            await bot._dismiss_popups()
            await bot._fill_and_submit_search()
            await bot.page.wait_for_timeout(1_500)
        return await parse_results(bot.page, config.journey.journey_date, config.journey.travel_class)
    finally:
        await bot.close()


def format_table(trains: list[Train], target_class: str) -> str:
    """Render the parsed trains as a fixed-width table."""
    tc = (target_class or "").upper()
    lines = [f"{'TRAIN':24} {'NO.':6} {'DEP':6} {'ARR':6} {tc + ' STATUS':14} {'FARE':7} OTHER"]
    lines.append("-" * 78)
    for t in trains:
        target = t.availability_for(tc)
        status = target.status_raw if target and target.status_raw else "—"
        fare = (target.fare if target else "") or "—"
        mark = "✓" if (target and target.bookable) else " "
        offered = " ".join(c.class_code for c in t.classes if not c.status_raw)
        lines.append(
            f"{mark}{t.name[:23]:23} {t.number:6} {t.departure:6} {t.arrival:6} "
            f"{status:14} {fare:7} {offered}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="irctc-search",
        description="One-shot IRCTC search + availability parse (no TUI).",
    )
    p.add_argument("--from", dest="from_station", default="SC", help="from station code")
    p.add_argument("--to", dest="to_station", default="TPTY", help="to station code")
    p.add_argument("--date", default="24-07-2026", help="journey date DD-MM-YYYY")
    p.add_argument("--class", dest="travel_class", default="SL", help="class code (SL/3A/2A/…)")
    p.add_argument("--quota", default="GENERAL", help="quota (GENERAL/TATKAL/…)")
    p.add_argument("--train", default="", help="target a specific train number")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--timeout", type=int, default=45_000, help="navigation timeout (ms)")
    p.add_argument("--results-url", default=None,
                   help="parse this already-rendered results page instead of searching")
    p.add_argument("--cdp", default="",
                   help="attach to a running browser over CDP (e.g. http://127.0.0.1:9222) "
                        "so the tool uses ITS network + your logged-in IRCTC session")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig(
        journey=JourneyConfig(
            from_station=args.from_station, to_station=args.to_station,
            journey_date=args.date, travel_class=args.travel_class,
            quota=args.quota, train_number=args.train,
        ),
        behavior=BehaviorConfig(headed=args.headed, save_screenshots=False, cdp_url=args.cdp),
    )

    def on_event(event):
        if event.message:
            print(f"· {event.message}")

    where = args.results_url or S.SEARCH_URL
    print(f"→ {args.from_station} → {args.to_station} on {args.date} ({args.travel_class}/{args.quota})")
    print(f"→ launching the tool's browser and opening {where}")
    try:
        trains = asyncio.run(
            run_search(config, results_url=args.results_url, timeout_ms=args.timeout, on_event=on_event)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n✗ could not complete the search: {str(exc).splitlines()[0]}")
        print("  (from this sandbox the browser can't reach IRCTC; on your machine it can.)")
        return 2

    if not trains:
        print("\nNo trains parsed.")
        return 1

    print("\n" + format_table(trains, args.travel_class))
    chosen = pick_target(trains, args.travel_class, args.train)
    if chosen:
        train, ca = chosen
        verdict = "BOOKABLE ✓" if ca.bookable else "not bookable (waitlist/regret)"
        print(f"\n➡ target: {train.label()} — {args.travel_class} {ca.status_raw or ca.availability.value} → {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

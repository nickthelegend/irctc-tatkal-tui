"""verify_selectors aggregation logic, exercised with a fake Playwright page."""

import asyncio

from irctc_tui import selectors as S
from irctc_tui.recon import verify_selectors


class _FakeLocator:
    def __init__(self, count):
        self._count = count

    async def count(self):
        return self._count


class _FakePage:
    """Stands in for a Playwright Page: maps selector string -> match count."""

    def __init__(self, mapping):
        self.mapping = mapping

    def locator(self, selector):
        return _FakeLocator(self.mapping.get(selector, 0))


def test_verify_reports_matches_per_group():
    # Make the first candidate of FROM/TO/SEARCH match; leave others at zero.
    page = _FakePage(
        {
            S.FROM_STATION_INPUT[0]: 1,
            S.TO_STATION_INPUT[0]: 2,
            S.SEARCH_BUTTON[0]: 1,
        }
    )
    results = asyncio.run(verify_selectors(page))
    matched = {name: any_hit for name, _hits, any_hit in results}

    assert matched["FROM_STATION_INPUT"] is True
    assert matched["TO_STATION_INPUT"] is True
    assert matched["SEARCH_BUTTON"] is True
    # Groups we didn't populate must report no match.
    assert matched["QUOTA_DROPDOWN"] is False
    assert matched["JOURNEY_DATE_INPUT"] is False


def test_verify_covers_all_core_groups():
    results = asyncio.run(verify_selectors(_FakePage({})))
    names = {name for name, _h, _a in results}
    assert {"FROM_STATION_INPUT", "TO_STATION_INPUT", "JOURNEY_DATE_INPUT",
            "QUOTA_DROPDOWN", "SEARCH_BUTTON"} <= names
    # With an empty page nothing matches.
    assert all(any_hit is False for _n, _h, any_hit in results)

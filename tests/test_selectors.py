"""Availability parsing and selector helpers."""

import pytest

from irctc_tui.automation import _class_label, _join, _label_is_before
from irctc_tui.selectors import Availability, classify_availability


@pytest.mark.parametrize(
    "text,expected",
    [
        ("AVAILABLE-0021", Availability.AVAILABLE),
        ("CURR_AVBL-0005", Availability.AVAILABLE),
        ("RAC 12", Availability.RAC),
        ("GNWL 34/WL 21", Availability.WAITLIST),
        ("REGRET/WL", Availability.WAITLIST),
        ("NOT AVAILABLE", Availability.NOT_AVAILABLE),
        ("TRAIN DEPARTED", Availability.NOT_AVAILABLE),
        ("CHART PREPARED", Availability.NOT_AVAILABLE),
        ("", Availability.UNKNOWN),
    ],
)
def test_classify_availability(text, expected):
    assert classify_availability(text) is expected


def test_bookable_flags():
    assert Availability.AVAILABLE.bookable
    assert Availability.RAC.bookable
    assert not Availability.WAITLIST.bookable
    assert not Availability.NOT_AVAILABLE.bookable


def test_join_drops_text_and_xpath_candidates():
    joined = _join(["a.css", "text=LOGIN", "xpath=//a", "b.css"])
    assert joined == "a.css, b.css"


def test_join_falls_back_to_first_when_no_css():
    assert _join(["text=LOGIN", "xpath=//a"]) == "text=LOGIN"


def test_class_label_maps_codes():
    assert "Sleeper" in _class_label("SL")
    assert _class_label("ZZ") == "ZZ"  # unknown passes through


def test_calendar_label_ordering():
    assert _label_is_before("june 2026", "july 2026")
    assert not _label_is_before("august 2026", "july 2026")
    assert _label_is_before("december 2025", "january 2026")

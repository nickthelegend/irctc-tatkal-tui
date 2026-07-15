"""Station dropdown helpers."""

from irctc_tui import stations


def test_known_route_stations_present():
    for code in ("SC", "HYB", "KCG", "TPTY", "RU"):
        assert stations.is_known(code)


def test_options_are_label_value_pairs():
    opts = stations.options()
    assert all(isinstance(label, str) and isinstance(value, str) for label, value in opts)
    # value is the code; label shows the code too
    codes = {value for _label, value in opts}
    assert {"SC", "TPTY"} <= codes


def test_unknown_current_code_is_prepended():
    opts = stations.options("ZZZ")
    assert opts[0][1] == "ZZZ"  # custom code available as an option
    assert "custom" in opts[0][0].lower()
    assert not stations.is_known("ZZZ")


def test_known_current_code_not_duplicated():
    opts = stations.options("SC")
    values = [v for _l, v in opts]
    assert values.count("SC") == 1

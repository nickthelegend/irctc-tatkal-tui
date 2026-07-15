"""Config model: round-trip, defaults, and validation."""

import json

from irctc_tui.config import AppConfig, Passenger, example_config


def test_example_config_round_trips():
    cfg = example_config()
    dumped = json.dumps(cfg.to_dict())
    restored = AppConfig.from_dict(json.loads(dumped))
    assert restored.to_dict() == cfg.to_dict()


def test_example_config_is_valid():
    assert example_config().validate() == []


def test_from_dict_ignores_unknown_keys_and_fills_defaults():
    cfg = AppConfig.from_dict({"journey": {"from_station": "SC", "bogus": 1}})
    assert cfg.journey.from_station == "SC"
    # a missing field falls back to the dataclass default
    assert cfg.journey.quota == "TATKAL"


def test_validate_flags_common_mistakes():
    cfg = AppConfig.from_dict(
        {
            "journey": {"from_station": "SC", "to_station": "SC", "journey_date": "2026-07-24",
                        "quota": "TATKAL", "travel_class": "SL"},
            "passengers": [{"name": "", "age": 0}],
            "timing": {"check_interval_seconds": 1},
        }
    )
    problems = cfg.validate()
    joined = " ".join(problems)
    assert "same" in joined                 # from == to
    assert "DD-MM-YYYY" in joined           # wrong date format
    assert "name is empty" in joined        # bad passenger
    assert "abusive" in joined              # interval too low


def test_save_and_load(tmp_path):
    path = tmp_path / "config.json"
    cfg = example_config()
    cfg.passengers.append(Passenger(name="Second", age=41, gender="Female"))
    cfg.save(path)
    assert path.exists()
    loaded = AppConfig.load(path)
    assert [p.name for p in loaded.passengers] == ["Passenger One", "Second"]


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = AppConfig.load(tmp_path / "nope.json")
    assert cfg.journey.from_station  # default present

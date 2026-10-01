from datetime import datetime

import pytest

from desk_controller import schedule
from desk_controller.cli import normalize_argv
from desk_controller.config import Config, Schedule

# 2026-10-01 is a Thursday.
THU = datetime(2026, 10, 1)


def sched(**kwargs) -> Schedule:
    return Schedule(**{"enabled": True, "times": {"10:00": "stand", "10:45": "sit"}, **kwargs})


def test_due_in_window():
    s = sched()
    assert schedule.due(s, THU.replace(hour=9, minute=59), THU.replace(hour=10)) == [
        (THU.replace(hour=10), "stand")
    ]
    # The window is (since, now]: a time equal to `since` was already handled.
    assert schedule.due(s, THU.replace(hour=10), THU.replace(hour=10, minute=1)) == []


def test_due_respects_enabled_and_days():
    window = (THU.replace(hour=9), THU.replace(hour=11))
    assert schedule.due(sched(enabled=False), *window) == []
    assert schedule.due(sched(days=["mon"]), *window) == []
    assert len(schedule.due(sched(days=["thu"]), *window)) == 2


def test_next_reminder_skips_to_next_working_day():
    friday_evening = datetime(2026, 10, 2, 18, 0)
    at, preset = schedule.next_reminder(sched(), friday_evening)
    assert (at, preset) == (datetime(2026, 10, 5, 10, 0), "stand")  # Monday


@pytest.mark.parametrize("raw, expected", [("9:05", "09:05"), (" 14:30 ", "14:30"), ("23:59", "23:59")])
def test_parse_time(raw, expected):
    assert schedule.parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["24:00", "9", "12:60", "noon"])
def test_parse_time_rejects(raw):
    with pytest.raises(ValueError):
        schedule.parse_time(raw)


def test_config_roundtrip_with_schedule(tmp_path):
    path = tmp_path / "config.toml"
    config = Config()
    config.presets["focus"] = 95.0
    config.schedule = Schedule(enabled=True, days=["mon", "wed"], times={"09:30": "focus", "08:00": "stand"})
    config.save(path)
    loaded = Config.load(path)
    assert loaded.schedule.enabled
    assert loaded.schedule.days == ["mon", "wed"]
    assert list(loaded.schedule.times) == ["08:00", "09:30"]
    assert loaded.presets["focus"] == 95.0


def test_menu_and_schedule_are_commands():
    assert normalize_argv(["menu"]) == ["menu"]
    assert normalize_argv(["schedule", "run"]) == ["schedule", "run"]

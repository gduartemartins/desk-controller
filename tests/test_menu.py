"""Menu flows, with the Omarchy dialogs replaced by scripted answers."""

import pytest

from desk_controller import menu, omarchy
from desk_controller.config import Config


@pytest.fixture
def script(monkeypatch):
    """Queue answers for omarchy.select / omarchy.ask; None means the dialog was dismissed."""
    answers: list = []
    shown: list = []

    def fake_select(prompt, options):
        labels = [o[1] for o in options]
        shown.append((prompt, labels))
        answer = answers.pop(0)
        assert answer is None or answer in labels, f"{answer!r} not offered in {prompt!r}: {labels}"
        return answer

    def fake_ask(prompt):
        shown.append((prompt, None))
        return answers.pop(0)

    monkeypatch.setattr(omarchy, "select", fake_select)
    monkeypatch.setattr(omarchy, "ask", fake_ask)
    monkeypatch.setattr(menu, "omarchy_error", lambda message: shown.append(("error", message)))
    return answers, shown


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    real_save = Config.save
    monkeypatch.setattr(Config, "save", lambda self, path=tmp_path / "config.toml": real_save(self, path))


def test_go_to_preset(script):
    answers, _ = script
    answers += ["Stand"]
    assert menu.run(Config()) == ["--notify", "go", "stand"]


def test_go_to_custom_height(script):
    answers, _ = script
    answers += ["Go to height…", "98,5"]
    assert menu.run(Config()) == ["--notify", "go", "98.5"]


def test_out_of_range_height_shows_error_and_stays(script):
    answers, shown = script
    answers += ["Go to height…", "200", None]
    assert menu.run(Config()) is None
    assert any(p == "error" and "outside" in m for p, m in shown)


def test_add_third_preset_and_set_height(script):
    answers, _ = script
    config = Config()
    answers += ["Presets", "Add preset…", "Focus", "95", "Set sit height", "72", "Back", None]
    assert menu.run(config) is None
    assert config.presets == {"sit": 72.0, "stand": 110.0, "focus": 95.0}


def test_save_current_height_returns_desk_command(script):
    answers, _ = script
    answers += ["Presets", "Save current height as…", "stand"]
    assert menu.run(Config()) == ["--notify", "save", "stand"]


def test_remove_preset_drops_its_reminders(script):
    answers, _ = script
    config = Config()
    config.presets["focus"] = 95.0
    config.schedule.times["12:00"] = "focus"
    answers += ["Presets", "Remove preset…", "focus", "Back", None]
    menu.run(config)
    assert "focus" not in config.presets
    assert "12:00" not in config.schedule.times


def test_reminders_turn_on_add_change_and_days(script):
    answers, _ = script
    config = Config()
    answers += [
        "Reminders",
        "Turn reminders on",
        "Add reminder…", "9:15", "stand",
        "10:00 → stand", "Change to sit",
        "10:45 → sit", "Remove",
        "Days", "Sat", "Fri", "Back",
        "Back", None,
    ]
    menu.run(config)
    sched = config.schedule
    assert sched.enabled
    assert sched.times == {"09:15": "stand", "10:00": "sit", "14:30": "stand", "15:15": "sit"}
    assert list(sched.times) == sorted(sched.times)
    assert sched.days == ["mon", "tue", "wed", "thu", "sat"]


def test_bad_reminder_time_shows_error(script):
    answers, shown = script
    answers += ["Reminders", "Add reminder…", "25:00", "Back", None]
    menu.run(Config())
    assert any(p == "error" and "HH:MM" in m for p, m in shown)

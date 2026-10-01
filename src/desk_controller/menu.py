"""`desk menu`: the Omarchy menu for presets, custom heights and reminders.

Menu pages only edit the config. Anything that talks to the desk is returned as
`desk` arguments for the CLI to run once the menu has closed.
"""

from datetime import datetime

from . import omarchy, schedule
from .config import WEEKDAYS, Config

ICON_DESK = "\U000F1239"
ICON_UP = "\U000F005D"
ICON_DOWN = "\U000F0045"
ICON_PRESET = "\U000F04CE"
ICON_RULER = "\U000F046D"
ICON_STOP = "\U000F04DB"
ICON_LIST = "\U000F0279"
ICON_BELL = "\U000F009A"
ICON_BELL_OFF = "\U000F009B"
ICON_CLOCK = "\U000F0150"
ICON_CALENDAR = "\U000F00ED"
ICON_ADD = "\U000F0415"
ICON_SAVE = "\U000F0193"
ICON_DELETE = "\U000F01B4"
ICON_BACK = "\U000F004D"
ICON_CHECK = "\U000F012C"
BACK = "Back"


class Back(Exception):
    """Dismissed or "Back": return to the previous page."""


def pick(prompt: str, options: list[tuple[str, ...]]) -> str:
    choice = omarchy.select(prompt, options)
    if choice is None or choice == BACK:
        raise Back
    return choice


def ask(prompt: str) -> str:
    answer = omarchy.ask(prompt)
    if answer is None:
        raise Back
    return answer


def preset_icon(name: str, config: Config) -> str:
    heights = sorted(config.presets.values())
    if len(heights) > 1 and config.presets[name] == heights[-1]:
        return ICON_UP
    if len(heights) > 1 and config.presets[name] == heights[0]:
        return ICON_DOWN
    return ICON_PRESET


def ask_height(prompt: str, config: Config) -> float:
    low, high = config.base_height_mm / 10, config.max_height_mm / 10
    text = ask(f"{prompt} in cm ({low:g}–{high:g})")
    try:
        value = round(float(text.replace(",", ".")), 1)
    except ValueError:
        raise MenuError(f"'{text}' isn't a number") from None
    if not low <= value <= high:
        raise MenuError(f"{value:g} cm is outside the desk's range ({low:g}–{high:g} cm)")
    return value


class MenuError(Exception):
    """Bad input; shown as a notification, then the page is shown again."""


def reminders_summary(config: Config) -> str:
    sched = config.schedule
    if not sched.enabled:
        return "Off"
    upcoming = schedule.next_reminder(sched, datetime.now())
    if not upcoming:
        return "On, nothing scheduled"
    at, preset = upcoming
    when = at.strftime("%H:%M") if at.date() == datetime.now().date() else at.strftime("%a %H:%M")
    return f"On · next: {preset} at {when}"


def days_summary(days: list[str]) -> str:
    if days == WEEKDAYS:
        return "Every day"
    if days == WEEKDAYS[:5]:
        return "Mon–Fri"
    return ", ".join(d.capitalize() for d in days) or "No days"


# --- pages -----------------------------------------------------------------


def main_page(config: Config) -> list[str] | None:
    while True:
        options = [(preset_icon(n, config), n.capitalize(), f"{h:g} cm") for n, h in config.presets.items()]
        options += [
            (ICON_RULER, "Go to height…"),
            (ICON_STOP, "Stop"),
            (ICON_LIST, "Presets", ", ".join(f"{n} {h:g}" for n, h in config.presets.items()) or "none"),
            (ICON_BELL if config.schedule.enabled else ICON_BELL_OFF, "Reminders", reminders_summary(config)),
        ]
        try:
            choice = pick("Desk", options)
        except Back:
            return None
        presets = {n.capitalize(): n for n in config.presets}
        try:
            if choice in presets:
                return ["--notify", "go", presets[choice]]
            if choice == "Go to height…":
                return ["--notify", "go", f"{ask_height('Height', config):g}"]
            if choice == "Stop":
                return ["--notify", "stop"]
            if choice == "Presets":
                result = presets_page(config)
                if result:
                    return result
            if choice == "Reminders":
                reminders_page(config)
        except Back:
            continue
        except MenuError as e:
            omarchy_error(str(e))


def presets_page(config: Config) -> list[str] | None:
    while True:
        options = [(ICON_PRESET, f"Set {n} height", f"{h:g} cm") for n, h in config.presets.items()]
        options += [
            (ICON_SAVE, "Save current height as…", "reads the desk's height now"),
            (ICON_ADD, "Add preset…"),
        ]
        if config.presets:
            options.append((ICON_DELETE, "Remove preset…"))
        options.append((ICON_BACK, BACK))
        choice = pick("Presets", options)
        try:
            if choice.startswith("Set ") and choice.endswith(" height"):
                name = choice[4:-7]
                config.presets[name] = ask_height(f"{name.capitalize()} height", config)
                config.save()
            elif choice == "Save current height as…":
                names = [(preset_icon(n, config), n, f"now {h:g} cm") for n, h in config.presets.items()]
                name = pick("Save current height as", [*names, (ICON_ADD, "New preset…"), (ICON_BACK, BACK)])
                if name == "New preset…":
                    name = ask_preset_name(config)
                return ["--notify", "save", name]
            elif choice == "Add preset…":
                name = ask_preset_name(config)
                config.presets[name] = ask_height(f"{name.capitalize()} height", config)
                config.save()
            elif choice == "Remove preset…":
                name = pick("Remove preset", [(ICON_DELETE, n, f"{h:g} cm") for n, h in config.presets.items()]
                            + [(ICON_BACK, BACK)])
                del config.presets[name]
                # Reminders pointing at a preset that no longer exists would fail when clicked.
                config.schedule.times = {t: p for t, p in config.schedule.times.items() if p != name}
                config.save()
        except Back:
            continue
        except MenuError as e:
            omarchy_error(str(e))


def ask_preset_name(config: Config) -> str:
    from .cli import COMMANDS, PRESET_NAME

    name = ask("Preset name (e.g. focus)").strip().lower()
    if not PRESET_NAME.match(name) or name in COMMANDS or name in ("menu", "schedule"):
        raise MenuError(f"'{name}' can't be used as a preset name (letters, digits, - and _ only)")
    return name


def reminders_page(config: Config) -> None:
    while True:
        sched = config.schedule
        toggle = "Turn reminders off" if sched.enabled else "Turn reminders on"
        options = [(ICON_BELL_OFF if sched.enabled else ICON_BELL, toggle, reminders_summary(config))]
        options += [(ICON_CLOCK, f"{t} → {p}", describe_preset(p, config)) for t, p in sched.times.items()]
        options += [
            (ICON_ADD, "Add reminder…"),
            (ICON_CALENDAR, "Days", days_summary(sched.days)),
            (ICON_BACK, BACK),
        ]
        choice = pick("Reminders", options)
        try:
            if choice == toggle:
                sched.enabled = not sched.enabled
                config.save()
            elif choice == "Add reminder…":
                at = schedule.parse_time(ask("Time (HH:MM, e.g. 14:30)"))
                preset = pick(f"At {at}, move to", [(preset_icon(n, config), n, f"{h:g} cm")
                                                    for n, h in config.presets.items()] + [(ICON_BACK, BACK)])
                sched.times = dict(sorted({**sched.times, at: preset}.items()))
                config.save()
            elif choice == "Days":
                days_page(config)
            elif " → " in choice:
                reminder_page(choice.split(" → ")[0], config)
        except Back:
            continue
        except (MenuError, ValueError) as e:
            omarchy_error(str(e))


def reminder_page(at: str, config: Config) -> None:
    current = config.schedule.times[at]
    others = [(preset_icon(n, config), f"Change to {n}", f"{h:g} cm")
              for n, h in config.presets.items() if n != current]
    choice = pick(f"{at} → {current}", [*others, (ICON_DELETE, "Remove"), (ICON_BACK, BACK)])
    if choice == "Remove":
        del config.schedule.times[at]
    else:
        config.schedule.times[at] = choice.removeprefix("Change to ")
    config.save()


def days_page(config: Config) -> None:
    while True:
        days = config.schedule.days
        options = [(ICON_CALENDAR, "Mon–Fri"), (ICON_CALENDAR, "Every day")]
        options += [(ICON_CHECK if d in days else " ", d.capitalize()) for d in WEEKDAYS]
        options.append((ICON_BACK, BACK))
        choice = pick(f"Remind on: {days_summary(days)}", options)
        if choice == "Mon–Fri":
            config.schedule.days = WEEKDAYS[:5]
        elif choice == "Every day":
            config.schedule.days = WEEKDAYS[:]
        else:
            day = choice.lower()
            chosen = set(days) ^ {day}
            config.schedule.days = [d for d in WEEKDAYS if d in chosen]
        config.save()


def describe_preset(preset: str, config: Config) -> str:
    height = config.presets.get(preset)
    return f"{height:g} cm" if height is not None else "missing preset"


def omarchy_error(message: str) -> None:
    from .cli import notify

    notify(message, urgent=True)


def run(config: Config) -> list[str] | None:
    return main_page(config)

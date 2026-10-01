"""Stand/sit reminders.

`desk schedule run` (the desk-reminders systemd user service) watches the clock
and, at each scheduled time, shows a notification. It never moves the desk on
its own: clicking the notification opens a menu to move now, snooze, or skip.
"""

import json
import logging
import re
import subprocess
import time
from datetime import datetime, timedelta

from . import omarchy
from .config import WEEKDAYS, Config, Schedule

log = logging.getLogger(__name__)

TIME_FORMAT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
CHECK_INTERVAL_S = 15
# After a suspend or a late start, still remind about times missed by up to this much.
GRACE = timedelta(minutes=5)
NOTIFICATION_ID = 7292
SNOOZE_CHOICES_MIN = (10, 30)


def parse_time(value: str) -> str:
    """Normalise "9:5"-style input to "HH:MM", or raise ValueError."""
    match = TIME_FORMAT.match(value.strip())
    if not match:
        raise ValueError(f"'{value}' isn't a time, use HH:MM (e.g. 14:30)")
    return f"{int(match[1]):02d}:{match[2]}"


def due(schedule: Schedule, since: datetime, now: datetime) -> list[tuple[datetime, str]]:
    """Scheduled (time, preset) pairs in the window (since, now]."""
    if not schedule.enabled:
        return []
    found = []
    day = since.date()
    while day <= now.date():
        if WEEKDAYS[day.weekday()] in schedule.days:
            for hhmm, preset in schedule.times.items():
                at = datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time())
                if since < at <= now:
                    found.append((at, preset))
        day += timedelta(days=1)
    return sorted(found)


def next_reminder(schedule: Schedule, now: datetime) -> tuple[datetime, str] | None:
    upcoming = due(schedule, now, now + timedelta(days=7))
    return upcoming[0] if upcoming else None


def describe(preset: str, config: Config) -> str:
    height = config.presets.get(preset)
    return f"{preset} ({height:g} cm)" if height is not None else preset


def remind(preset: str, config: Config, desk_bin: str) -> None:
    """Show the reminder; clicking it opens the move/snooze/skip menu."""
    action = [desk_bin, "schedule", "prompt", preset]
    subprocess.run(
        [
            "notify-send", "--app-name=Desk", f"--replace-id={NOTIFICATION_ID}", "--urgency=critical",
            f"--hint=string:omarchy-exec-argv:{json.dumps(action)}",
            f"Time to {preset}",
            f"Click to move the desk to {describe(preset, config)}, snooze or skip.",
        ],
        check=False,
        timeout=10,
    )


def prompt(preset: str, config: Config, desk_bin: str) -> list[str] | None:
    """Ask what to do about a reminder. Returns `desk` arguments to run, if any."""
    move = f"Move to {describe(preset, config)}"
    snoozes = {f"Snooze {m} min": m for m in SNOOZE_CHOICES_MIN}
    choice = omarchy.select(
        f"Time to {preset}",
        [("\U000F1239", move), *(("\U000F04B2", label) for label in snoozes), ("\U000F0156", "Skip")],
    )
    if choice == move:
        return ["--notify", "go", preset]
    if choice in snoozes:
        snooze(preset, snoozes[choice], desk_bin)
    return None


def snooze(preset: str, minutes: int, desk_bin: str) -> None:
    subprocess.run(
        ["systemd-run", "--user", "--quiet", f"--on-active={minutes}m", "--timer-property=AccuracySec=1s",
         desk_bin, "schedule", "remind", preset],
        check=False,
        timeout=10,
    )


def run(desk_bin: str) -> None:
    """Service loop. Re-reads the config every check, so menu edits apply immediately."""
    last = datetime.now()
    log.info("reminder service started")
    while True:
        time.sleep(CHECK_INTERVAL_S)
        now = datetime.now()
        try:
            config = Config.load()
        except (OSError, ValueError) as e:
            log.warning("can't read config: %s", e)
            last = now
            continue
        for at, preset in due(config.schedule, max(last, now - GRACE), now):
            log.info("reminder for %s at %s", preset, at.strftime("%H:%M"))
            try:
                remind(preset, config, desk_bin)
            except (OSError, subprocess.TimeoutExpired) as e:
                log.warning("notification failed: %s", e)
        last = now

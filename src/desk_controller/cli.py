"""The `desk` command.

    desk stand | desk sit | desk 105     go to a preset or a height in cm
    desk up / desk down [CM]             nudge (default 2 cm)
    desk status | stop | save NAME | presets | scan | setup [ADDRESS]

Only one command talks to the desk at a time. A new movement command cancels
one still in progress, so hitting "sit" while it's rising to "stand" just works.
"""

import argparse
import asyncio
import fcntl
import logging
import os
import re
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from bleak.exc import BleakError

from . import desk as desk_mod
from .config import FAKE_STATE_FILE, Config
from .desk import BluetoothDesk, Desk, DeskError
from .fake import FakeDesk

COMMANDS = {"go", "up", "down", "stop", "status", "save", "presets", "scan", "setup"}
PRESET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
LOCK_FILE = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "desk-controller.lock"


class UserError(Exception):
    pass


@contextmanager
def desk_lock(takeover: bool):
    """Hold the single-instance lock, optionally cancelling whoever holds it now."""
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.pread(fd, 16, 0).strip()
            if takeover and holder:
                try:
                    os.kill(int(holder), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.ftruncate(fd, 0)
        os.pwrite(fd, str(os.getpid()).encode(), 0)
        yield
    finally:
        os.close(fd)


def make_desk(config: Config, fake: bool) -> Desk:
    limits = dict(base_height_mm=config.base_height_mm, max_height_mm=config.max_height_mm)
    if fake:
        return FakeDesk(state_file=FAKE_STATE_FILE, **limits)
    if not config.address:
        raise UserError("No desk configured yet. Run `desk setup` first.")
    return BluetoothDesk(config.address, **limits)


def resolve_target(value: str, config: Config) -> float:
    """Turn a preset name or a number of cm into millimetres."""
    if value in config.presets:
        return config.presets[value] * 10
    try:
        return float(value) * 10
    except ValueError:
        names = ", ".join(config.presets) or "none"
        raise UserError(f"Unknown preset '{value}' (presets: {names})") from None


def cm(mm: float) -> str:
    return f"{mm / 10:.1f} cm"


async def cmd_scan(args, config: Config) -> str:
    desks = await desk_mod.scan()
    if not desks:
        return "No desks found. Press the Bluetooth button on the desk controller and try again."
    return "\n".join(f"{d.address}  {d.name:<16} signal {d.rssi} dBm" for d in desks)


async def cmd_setup(args, config: Config) -> str:
    if args.fake:
        address = "fake"
    elif args.address:
        address = args.address
    else:
        desks = await desk_mod.scan()
        if not desks:
            raise UserError(
                "No desks found. Hold the Bluetooth button on the desk controller "
                "until the light blinks blue, then run `desk setup` again."
            )
        if len(desks) > 1:
            listing = "\n".join(f"  {d.address}  {d.name}" for d in desks)
            raise UserError(f"Found several desks, pick one with `desk setup ADDRESS`:\n{listing}")
        address = desks[0].address

    config.address = address
    async with make_desk(config, args.fake) as desk:
        height = await desk.height_mm()
    config.save()
    return f"Connected to {address}, desk is at {cm(height)}. Saved to config."


async def cmd_status(args, config: Config) -> str:
    async with make_desk(config, args.fake) as desk:
        return cm(await desk.height_mm())


async def cmd_go(args, config: Config) -> str:
    target = resolve_target(args.target, config)
    async with make_desk(config, args.fake) as desk:
        return f"Desk at {cm(await desk.move_to(target))}"


async def cmd_nudge(args, config: Config) -> str:
    step = args.cm * 10 * (1 if args.command == "up" else -1)
    async with make_desk(config, args.fake) as desk:
        current = await desk.height_mm()
        target = min(max(current + step, desk.base_height_mm), desk.max_height_mm)
        return f"Desk at {cm(await desk.move_to(target))}"


async def cmd_stop(args, config: Config) -> str:
    async with make_desk(config, args.fake) as desk:
        await desk.stop()
        return f"Stopped at {cm(await desk.height_mm())}"


async def cmd_save(args, config: Config) -> str:
    if not PRESET_NAME.match(args.name) or args.name in COMMANDS:
        raise UserError(f"'{args.name}' can't be used as a preset name")
    async with make_desk(config, args.fake) as desk:
        height = await desk.height_mm()
    config.presets[args.name] = round(height / 10, 1)
    config.save()
    return f"Saved '{args.name}' = {cm(height)}"


async def cmd_presets(args, config: Config) -> str:
    return "\n".join(f"{name:<10} {value:g} cm" for name, value in config.presets.items())


HANDLERS = {
    "scan": (cmd_scan, None),
    "setup": (cmd_setup, "wait"),
    "status": (cmd_status, "wait"),
    "go": (cmd_go, "takeover"),
    "up": (cmd_nudge, "takeover"),
    "down": (cmd_nudge, "takeover"),
    "stop": (cmd_stop, "takeover"),
    "save": (cmd_save, "wait"),
    "presets": (cmd_presets, None),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk", description="Control a LINAK / IKEA IDÅSEN desk.")
    parser.add_argument("--fake", action="store_true", default=bool(os.environ.get("DESK_FAKE")),
                        help="use the simulated desk (or set DESK_FAKE=1)")
    parser.add_argument("--notify", action="store_true", help="show the result as a desktop notification")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="list desks nearby")
    p = sub.add_parser("setup", help="pair with a desk and save it as the default")
    p.add_argument("address", nargs="?")
    sub.add_parser("status", help="print the current height")
    p = sub.add_parser("go", help="move to a preset or a height in cm")
    p.add_argument("target")
    for direction in ("up", "down"):
        p = sub.add_parser(direction, help=f"nudge {direction}")
        p.add_argument("cm", nargs="?", type=float, default=2.0)
    sub.add_parser("stop", help="stop moving")
    p = sub.add_parser("save", help="save the current height as a preset")
    p.add_argument("name")
    sub.add_parser("presets", help="list presets")
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    """Allow `desk stand` / `desk 105` as shorthand for `desk go ...`."""
    for i, arg in enumerate(argv):
        if not arg.startswith("-"):
            if arg not in COMMANDS:
                return argv[:i] + ["go"] + argv[i:]
            break
    return argv


def notify(message: str, urgent: bool = False) -> None:
    cmd = ["notify-send", "--app-name=Desk", "--replace-id=7291", "Desk", message]
    if urgent:
        cmd.insert(1, "--urgency=critical")
    try:
        subprocess.run(cmd, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


async def run(handler, args, config: Config) -> str:
    # SIGTERM (sent by a newer command taking over) cancels us; move_to then stops the desk.
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    return await handler(args, config)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))
    logging.basicConfig(
        level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
        format="%(levelname)s %(name)s: %(message)s",
    )
    handler, locking = HANDLERS[args.command]
    config = Config.load()

    try:
        if locking:
            with desk_lock(takeover=locking == "takeover"):
                message = asyncio.run(run(handler, args, config))
        else:
            message = asyncio.run(run(handler, args, config))
    except (UserError, DeskError, BleakError, ValueError) as e:
        print(f"desk: {e}", file=sys.stderr)
        if args.notify:
            notify(str(e), urgent=True)
        return 1
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("desk: cancelled", file=sys.stderr)
        return 130

    print(message)
    if args.notify:
        notify(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())

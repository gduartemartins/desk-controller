import asyncio
import concurrent.futures
import threading
import time
from datetime import datetime, timedelta

import pytest

tk = pytest.importorskip("tkinter")

from desk_controller import desk as desk_mod, protocol, windows
from desk_controller.config import WEEKDAYS, Config
from desk_controller.fake import FakeDesk
from desk_controller.windows_session import DeskSession
from desk_controller.windows_worker import BusyError, DeskWorker


class FakeTray:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.mark.parametrize("text, expected", [("62", 62), ("127", 127), ("98,5", 98.5)])
def test_height_validation_accepts_range_and_decimal_comma(text, expected):
    assert windows.parse_height(text, Config()) == expected


@pytest.mark.parametrize("text", ["nan", "inf", "61.9", "127.1", "abc", ""])
def test_height_validation_rejects_bad_values(text):
    with pytest.raises(ValueError):
        windows.parse_height(text, Config())


async def test_windows_connection_uses_bleak_not_bluez(monkeypatch):
    writes = []

    class Client:
        def __init__(self, address, **kwargs):
            assert address == "AA:BB:CC:DD:EE:FF"
            assert kwargs["pair"] is True

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def write_gatt_char(self, uuid, data):
            writes.append((uuid, data))

        async def read_gatt_char(self, uuid):
            assert uuid == protocol.UUID_HEIGHT
            return protocol.encode_height(900) + b"\x00\x00"

    async def no_bluez(_address):
        raise AssertionError("Windows must not invoke BlueZ")

    monkeypatch.setattr(desk_mod.sys, "platform", "win32")
    monkeypatch.setattr(desk_mod, "bluez_connected", no_bluez)
    monkeypatch.setattr(desk_mod, "BleakClient", Client)
    async with desk_mod.BluetoothDesk("AA:BB:CC:DD:EE:FF") as desk:
        assert await desk.height_mm() == 900
        await desk.stop()
    assert writes == [
        (protocol.UUID_DPG, protocol.DPG_USER_QUERY),
        (protocol.UUID_DPG, protocol.DPG_USER_REGISTER),
        (protocol.UUID_COMMAND, protocol.COMMAND_STOP),
        (protocol.UUID_REFERENCE_INPUT, protocol.REFERENCE_INPUT_STOP),
    ]


async def test_session_reuses_connection_and_reconnects_after_disconnect():
    created = []

    def factory(address):
        desk = FakeDesk()
        created.append((address, desk))
        return desk

    session = DeskSession(factory)
    async def read(desk):
        return await desk.height_mm()

    assert await session.run("desk", read) == 720
    assert await session.run("desk", read) == 720
    assert len(created) == 1
    await created[0][1].disconnect()
    assert await session.run("desk", read) == 720
    assert len(created) == 2
    await session.close()
    assert not created[1][1].connected


async def test_cancelled_movement_stops_and_disconnects():
    desk = FakeDesk(height_mm=720, speed_mm_s=100)
    session = DeskSession(lambda address: desk)

    async def move(current):
        return await current.move_to(1200, poll_interval=0.01)

    task = asyncio.create_task(session.run("desk", move))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stopped_at = desk.position_mm
    await asyncio.sleep(0.05)
    desk._advance()
    assert desk.position_mm == pytest.approx(stopped_at, abs=0.1)
    assert not desk.connected
    assert session.desk is None


def test_takeover_waits_for_cancelled_movement_to_stop():
    worker = DeskWorker()
    moving = threading.Event()
    stopped = threading.Event()

    async def first():
        try:
            moving.set()
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0.05)
            stopped.set()

    async def second():
        assert stopped.is_set()
        return "stopped"

    try:
        old = worker.submit(first)
        assert moving.wait(2)
        busy = worker.submit(second)
        with pytest.raises(BusyError):
            busy.result(timeout=2)
        new = worker.submit(second, takeover=True)
        assert new.result(timeout=2) == "stopped"
        with pytest.raises(concurrent.futures.CancelledError):
            old.result(timeout=2)
    finally:
        worker.shutdown().result(timeout=2)
        worker.thread.join(timeout=2)
    assert not worker.thread.is_alive()


def test_gui_simulator_setup_move_and_presets(monkeypatch, tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Windows desktop unavailable: {error}")
    root.withdraw()
    monkeypatch.setattr(windows, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(windows, "FAKE_STATE_FILE", tmp_path / "fake.json")
    errors = []
    monkeypatch.setattr(windows.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    app = windows.WindowsApp(root, fake=True, tray_factory=FakeTray)
    created = []

    def make_desk(address=None):
        desk = FakeDesk(speed_mm_s=2000, state_file=windows.FAKE_STATE_FILE)
        created.append(desk)
        return desk

    monkeypatch.setattr(app, "make_desk", make_desk)

    def wait_for(text):
        deadline = time.monotonic() + 5
        while text not in app.status.get() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
            assert not errors, errors
        assert text in app.status.get()

    try:
        app.setup()
        wait_for("Connected and saved")
        app.height.set("75")
        app.go_height()
        wait_for("Desk at")
        assert app.current_height.get() == "75.0 cm"
        assert app.height_progress.get() == pytest.approx(20)
        app.preset.set("focus")
        app.save_current()
        wait_for("Saved focus")
        assert Config.load(windows.CONFIG_FILE).presets["focus"] == pytest.approx(75, abs=0.1)
        app.reminder_time.set("09:15")
        app.add_reminder()
        assert Config.load(windows.CONFIG_FILE).schedule.times["09:15"] == "focus"
        assert len(created) == 1
        assert created[0].connected
        app.hide()
        root.update()
        assert not root.winfo_viewable()
        app.tray.dispatch("show")
        deadline = time.monotonic() + 2
        while not root.winfo_viewable() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert root.winfo_viewable()
        app.current_height.set("--.- cm")
        app._refresh_live_height()
        deadline = time.monotonic() + 2
        while app.current_height.get() == "--.- cm" and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert app.current_height.get() == "75.0 cm"
        assert app.height_progress.get() == pytest.approx(20)
        app.hide()
        reminders = []
        monkeypatch.setattr(app, "show_reminder", reminders.append)
        now = datetime.now()
        app.last_check = now.replace(second=0, microsecond=0) - timedelta(seconds=1)
        app.config.schedule.enabled = True
        app.config.schedule.days = [WEEKDAYS[now.weekday()]]
        app.config.schedule.times = {now.strftime("%H:%M"): "stand"}
        app._check_reminders()
        assert reminders == ["stand"]
        app.tray.dispatch("stop")
        wait_for("Stopped at")
        assert not root.winfo_viewable()
    finally:
        app.close()
        deadline = time.monotonic() + 5
        while not app.tray.stopped and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert not app.worker.thread.is_alive()
        assert not created[0].connected
        assert app.tray.stopped
        try:
            root.destroy()
        except tk.TclError:
            pass

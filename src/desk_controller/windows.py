"""Windows desktop controller for LINAK / IKEA IDÅSEN desks."""

import argparse
import concurrent.futures
import math
import os
import queue
import re
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from . import desk as desk_mod, schedule
from .config import Config, WEEKDAYS
from .desk import BluetoothDesk
from .fake import FakeDesk
from .windows_session import DeskSession
from .windows_worker import DeskWorker


APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "desk-controller"
CONFIG_FILE = APP_DIR / "config.toml"
FAKE_STATE_FILE = APP_DIR / "fake-desk.json"
PRESET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
DESK_ADDRESS = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


def parse_height(text, config):
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        raise ValueError("Enter a height in centimetres.") from None
    low, high = config.base_height_mm / 10, config.max_height_mm / 10
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Height must be between {low:g} and {high:g} cm.")
    return value


class WindowsApp:
    def __init__(self, root, *, fake=False):
        self.root = root
        self.fake = fake
        self.config = Config.load(CONFIG_FILE)
        self.worker = DeskWorker()
        self.session = DeskSession(lambda address: self.make_desk(address))
        self.results = queue.Queue()
        self.last_check = datetime.now()
        self.closing = False
        self.desks = []
        self.root.title("Desk Controller" + (" - simulator" if fake else ""))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status = tk.StringVar(value="Ready")
        self.address = tk.StringVar(value=self.config.address or "")
        self.height = tk.StringVar()
        self.preset = tk.StringVar()
        self.preset_height = tk.StringVar()
        self.reminder_time = tk.StringVar()
        self.enabled = tk.BooleanVar(value=self.config.schedule.enabled)
        self.days = {day: tk.BooleanVar(value=day in self.config.schedule.days) for day in WEEKDAYS}
        self._build()
        self.refresh_presets()
        self.refresh_reminders()
        self.root.after(100, self._poll_results)
        self.root.after(1000, self._check_reminders)
        if self.config.address and (self.fake or DESK_ADDRESS.fullmatch(self.config.address)):
            self.root.after_idle(self.read_height)

    def _build(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Desk").grid(row=0, column=0, sticky="w")
        self.desk_list = ttk.Combobox(frame, textvariable=self.address, width=38)
        self.desk_list.grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(frame, text="Scan", command=self.scan).grid(row=0, column=2)
        ttk.Button(frame, text="Connect / save", command=self.setup).grid(row=0, column=3, padx=5)

        controls = ttk.LabelFrame(frame, text="Move desk", padding=8)
        controls.grid(row=1, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Button(controls, text="Refresh height", command=self.read_height).grid(row=0, column=0)
        ttk.Label(controls, text="Height (cm)").grid(row=0, column=1, padx=5)
        ttk.Entry(controls, textvariable=self.height, width=9).grid(row=0, column=2)
        ttk.Button(controls, text="Go", command=self.go_height).grid(row=0, column=3, padx=5)
        ttk.Button(controls, text="Up 2 cm", command=lambda: self.nudge(2)).grid(row=1, column=0, pady=5)
        ttk.Button(controls, text="Down 2 cm", command=lambda: self.nudge(-2)).grid(row=1, column=1)
        ttk.Button(controls, text="STOP", command=self.stop).grid(row=1, column=3)

        presets = ttk.LabelFrame(frame, text="Presets", padding=8)
        presets.grid(row=2, column=0, columnspan=4, sticky="ew", pady=4)
        ttk.Label(presets, text="Name").grid(row=0, column=0)
        self.preset_list = ttk.Combobox(presets, textvariable=self.preset, width=17)
        self.preset_list.grid(row=0, column=1, padx=5)
        self.preset_list.bind("<<ComboboxSelected>>", self._preset_selected)
        ttk.Label(presets, text="Height (cm)").grid(row=0, column=2)
        ttk.Entry(presets, textvariable=self.preset_height, width=9).grid(row=0, column=3, padx=5)
        ttk.Button(presets, text="Go to preset", command=self.go_preset).grid(row=1, column=0, pady=5)
        ttk.Button(presets, text="Set / add", command=self.set_preset).grid(row=1, column=1)
        ttk.Button(presets, text="Save current", command=self.save_current).grid(row=1, column=2)
        ttk.Button(presets, text="Remove", command=self.remove_preset).grid(row=1, column=3)

        reminders = ttk.LabelFrame(frame, text="Reminders (while app is open)", padding=8)
        reminders.grid(row=3, column=0, columnspan=4, sticky="ew", pady=4)
        ttk.Checkbutton(reminders, text="Enabled", variable=self.enabled,
                        command=self.save_days).grid(row=0, column=0)
        for i, day in enumerate(WEEKDAYS):
            ttk.Checkbutton(reminders, text=day.capitalize(), variable=self.days[day],
                            command=self.save_days).grid(row=0, column=i + 1)
        ttk.Label(reminders, text="Time (HH:MM)").grid(row=1, column=0, pady=5)
        ttk.Entry(reminders, textvariable=self.reminder_time, width=8).grid(row=1, column=1)
        ttk.Button(reminders, text="Add for selected preset", command=self.add_reminder).grid(
            row=1, column=2, columnspan=3)
        self.reminder_list = tk.Listbox(reminders, height=4, exportselection=False)
        self.reminder_list.grid(row=2, column=0, columnspan=6, sticky="ew")
        ttk.Button(reminders, text="Remove", command=self.remove_reminder).grid(row=2, column=6, columnspan=2)
        ttk.Label(frame, textvariable=self.status, wraplength=650).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=8)

    def make_desk(self, address=None):
        limits = dict(base_height_mm=self.config.base_height_mm, max_height_mm=self.config.max_height_mm)
        if self.fake:
            return FakeDesk(state_file=FAKE_STATE_FILE, **limits)
        address = address or self.config.address
        if not address:
            raise ValueError("Scan and connect to a desk first.")
        return BluetoothDesk(address, **limits)

    def run(self, operation, on_success, *, takeover=False):
        if self.closing:
            return
        self.status.set("Working...")
        future = self.worker.submit(operation, takeover=takeover)
        future.add_done_callback(lambda f: self.results.put((f, on_success)))

    def run_desk(self, operation, on_success, *, address=None, takeover=False):
        selected = "fake" if self.fake else (address or self.config.address)

        async def run():
            return await self.session.run(selected, operation)

        self.run(run, on_success, takeover=takeover)

    def _poll_results(self):
        while True:
            try:
                future, callback = self.results.get_nowait()
            except queue.Empty:
                break
            try:
                value = future.result()
            except concurrent.futures.CancelledError:
                continue
            except Exception as error:
                self.status.set(f"Error: {error}")
                messagebox.showerror("Desk Controller", str(error), parent=self.root)
            else:
                try:
                    callback(value)
                except (OSError, ValueError) as error:
                    self.status.set(f"Error: {error}")
                    messagebox.showerror("Desk Controller", str(error), parent=self.root)
        if not self.closing:
            self.root.after(100, self._poll_results)

    def scan(self):
        async def find():
            await self.session.close()
            return await desk_mod.scan()

        def show(found):
            self.desks = found
            self.desk_list["values"] = [f"{d.address}  {d.name}" for d in found]
            if found:
                self.desk_list.current(0)
                self.status.set(f"Found {len(found)} desk(s). Select one and connect.")
            else:
                self.status.set("No desks found. Put the desk in Bluetooth pairing mode and try again.")

        self.run(find, show)

    def setup(self):
        address = self.address.get().strip().split("  ", 1)[0]
        if self.fake:
            address = "fake"
        if not address:
            messagebox.showerror("Desk Controller", "Select a desk or enter its address.", parent=self.root)
            return
        if not self.fake and not DESK_ADDRESS.fullmatch(address):
            messagebox.showerror("Desk Controller", "Enter a Bluetooth address like AA:BB:CC:DD:EE:FF.",
                                 parent=self.root)
            return

        async def connect(desk):
            return await desk.height_mm()

        def saved(height):
            self.config.address = address
            self.config.save(CONFIG_FILE)
            self.address.set(address)
            self.status.set(f"Connected and saved. Height: {height / 10:.1f} cm")

        self.run_desk(connect, saved, address=address)

    def read_height(self):
        async def read(desk):
            return await desk.height_mm()
        self.run_desk(read, lambda height: self.status.set(f"Height: {height / 10:.1f} cm"))

    def move(self, target):
        async def go(desk):
            return await desk.move_to(target * 10)
        self.run_desk(go, lambda height: self.status.set(f"Desk at {height / 10:.1f} cm"), takeover=True)

    def go_height(self):
        try:
            self.move(parse_height(self.height.get(), self.config))
        except ValueError as error:
            messagebox.showerror("Desk Controller", str(error), parent=self.root)

    def go_preset(self):
        name = self.preset.get().strip()
        if name not in self.config.presets:
            messagebox.showerror("Desk Controller", "Select an existing preset.", parent=self.root)
            return
        self.move(self.config.presets[name])

    def nudge(self, amount):
        async def move(desk):
            current = await desk.height_mm()
            target = min(max(current + amount * 10, desk.base_height_mm), desk.max_height_mm)
            return await desk.move_to(target)
        self.run_desk(move, lambda height: self.status.set(f"Desk at {height / 10:.1f} cm"), takeover=True)

    def stop(self):
        async def stop_desk(desk):
            await desk.stop()
            return await desk.height_mm()
        self.run_desk(stop_desk, lambda height: self.status.set(f"Stopped at {height / 10:.1f} cm"), takeover=True)

    def preset_name(self):
        name = self.preset.get().strip().lower()
        if not PRESET_NAME.fullmatch(name):
            raise ValueError("Preset names may contain only letters, digits, hyphens and underscores.")
        return name

    def set_preset(self):
        try:
            name = self.preset_name()
            height = parse_height(self.preset_height.get(), self.config)
            self.config.presets[name] = height
            self.config.save(CONFIG_FILE)
        except (ValueError, OSError) as error:
            messagebox.showerror("Desk Controller", str(error), parent=self.root)
            return
        self.preset.set(name)
        self.refresh_presets()
        self.status.set(f"Saved {name}: {height:g} cm")

    def save_current(self):
        try:
            name = self.preset_name()
        except ValueError as error:
            messagebox.showerror("Desk Controller", str(error), parent=self.root)
            return

        async def read(desk):
            return await desk.height_mm()

        def save(height):
            self.config.presets[name] = round(height / 10, 1)
            self.config.save(CONFIG_FILE)
            self.preset.set(name)
            self.refresh_presets()
            self.status.set(f"Saved {name}: {height / 10:.1f} cm")
        self.run_desk(read, save)

    def remove_preset(self):
        name = self.preset.get().strip()
        if name not in self.config.presets:
            messagebox.showerror("Desk Controller", "Select an existing preset.", parent=self.root)
            return
        if not messagebox.askyesno("Remove preset", f"Remove {name} and its reminders?", parent=self.root):
            return
        del self.config.presets[name]
        self.config.schedule.times = {t: p for t, p in self.config.schedule.times.items() if p != name}
        self.save_config()
        self.preset.set("")
        self.refresh_presets()
        self.refresh_reminders()

    def _preset_selected(self, _event=None):
        name = self.preset.get()
        self.preset_height.set(f"{self.config.presets[name]:g}" if name in self.config.presets else "")

    def refresh_presets(self):
        self.preset_list["values"] = list(self.config.presets)
        self._preset_selected()

    def save_config(self):
        try:
            self.config.save(CONFIG_FILE)
        except OSError as error:
            self.status.set(f"Error saving settings: {error}")
            messagebox.showerror("Desk Controller", str(error), parent=self.root)

    def save_days(self):
        self.config.schedule.enabled = self.enabled.get()
        self.config.schedule.days = [day for day, selected in self.days.items() if selected.get()]
        self.save_config()

    def add_reminder(self):
        try:
            at = schedule.parse_time(self.reminder_time.get())
            name = self.preset.get().strip()
            if name not in self.config.presets:
                raise ValueError("Select an existing preset for the reminder.")
            self.config.schedule.times[at] = name
            self.config.schedule.times = dict(sorted(self.config.schedule.times.items()))
            self.save_config()
            self.refresh_reminders()
        except ValueError as error:
            messagebox.showerror("Desk Controller", str(error), parent=self.root)

    def refresh_reminders(self):
        self.reminder_list.delete(0, tk.END)
        for at, name in sorted(self.config.schedule.times.items()):
            self.reminder_list.insert(tk.END, f"{at}  -  {name}")

    def remove_reminder(self):
        selected = self.reminder_list.curselection()
        if not selected:
            return
        at = sorted(self.config.schedule.times)[selected[0]]
        del self.config.schedule.times[at]
        self.save_config()
        self.refresh_reminders()

    def _check_reminders(self):
        now = datetime.now()
        for _, name in schedule.due(self.config.schedule, max(self.last_check, now - schedule.GRACE), now):
            self.show_reminder(name)
        self.last_check = now
        if not self.closing:
            self.root.after(schedule.CHECK_INTERVAL_S * 1000, self._check_reminders)

    def show_reminder(self, name):
        dialog = tk.Toplevel(self.root)
        dialog.title("Desk reminder")
        dialog.attributes("-topmost", True)
        ttk.Label(dialog, text=f"Time to {schedule.describe(name, self.config)}", padding=15).pack()
        buttons = ttk.Frame(dialog, padding=10)
        buttons.pack()

        def move():
            dialog.destroy()
            if name in self.config.presets:
                self.move(self.config.presets[name])
            else:
                messagebox.showerror("Desk Controller", f"Preset '{name}' no longer exists.", parent=self.root)

        def snooze(minutes):
            dialog.destroy()
            self.root.after(minutes * 60 * 1000, lambda: self.show_reminder(name))

        ttk.Button(buttons, text="Move now", command=move).pack(side="left", padx=3)
        for minutes in schedule.SNOOZE_CHOICES_MIN:
            ttk.Button(buttons, text=f"Snooze {minutes} min",
                       command=lambda m=minutes: snooze(m)).pack(side="left", padx=3)
        ttk.Button(buttons, text="Skip", command=dialog.destroy).pack(side="left", padx=3)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.status.set("Stopping and disconnecting...")
        self.shutdown_result = self.worker.shutdown(cleanup=self.session.close)
        self._wait_for_worker()

    def _wait_for_worker(self):
        if self.worker.thread.is_alive():
            self.root.after(100, self._wait_for_worker)
        else:
            try:
                self.shutdown_result.result()
            except Exception as error:
                messagebox.showerror("Desk Controller", f"Could not disconnect: {error}", parent=self.root)
            self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows desk controller")
    parser.add_argument("--fake", action="store_true", help="use a simulated desk")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("desk-windows is only supported on Windows")

    # A separate process must not issue competing movement commands.
    import msvcrt

    APP_DIR.mkdir(parents=True, exist_ok=True)
    lock = (APP_DIR / "windows.lock").open("a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        messagebox.showerror("Desk Controller", "Desk Controller is already running.")
        return 1
    try:
        root = tk.Tk()
        try:
            WindowsApp(root, fake=args.fake)
        except (OSError, ValueError) as error:
            messagebox.showerror("Desk Controller", f"Could not load settings: {error}", parent=root)
            root.destroy()
            return 1
        root.mainloop()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

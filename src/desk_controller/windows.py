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
from .windows_tray import TrayIcon
from .windows_worker import BusyError, DeskWorker


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
    def __init__(self, root, *, fake=False, tray_factory=TrayIcon):
        self.root = root
        self.fake = fake
        self.config = Config.load(CONFIG_FILE)
        self.tray_actions = queue.Queue()
        self.tray = tray_factory(self.tray_actions.put)
        self.worker = DeskWorker()
        self.session = DeskSession(lambda address: self.make_desk(address))
        self.results = queue.Queue()
        self.height_updates = queue.Queue()
        self.last_check = datetime.now()
        self.closing = False
        self.live_height_enabled = True
        self.desks = []
        self.root.title("Desk Controller" + (" - simulator" if fake else ""))
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        self.status = tk.StringVar(value="Ready")
        self.current_height = tk.StringVar(value="--.- cm")
        self.height_progress = tk.DoubleVar(value=0)
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
        self.root.after(2000, self._refresh_live_height)
        if self.config.address and (self.fake or DESK_ADDRESS.fullmatch(self.config.address)):
            self.root.after_idle(self.read_height)

    def _build(self):
        self.root.minsize(700, 600)
        self.root.configure(background="#09151e")
        self.root.option_add("*TCombobox*Listbox.background", "#162b37")
        self.root.option_add("*TCombobox*Listbox.foreground", "#e7f4f6")
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#245363")
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#09151e", foreground="#e7f4f6")
        style.configure("TFrame", background="#09151e")
        style.configure("TLabel", background="#09151e", foreground="#e7f4f6")
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"), foreground="#f2fbfc")
        style.configure("Brand.TLabel", font=("Segoe UI", 21, "bold"), foreground="#f2fbfc")
        style.configure("Caption.TLabel", foreground="#9bb6bf")
        style.configure("Section.TLabel", font=("Segoe UI", 9, "bold"), foreground="#63ddd8")
        style.configure("Height.TLabel", font=("Segoe UI", 40, "bold"), foreground="#f2fbfc")
        style.configure("TButton", padding=(12, 8), background="#203946", foreground="#e7f4f6",
                        borderwidth=0, focuscolor="#63ddd8")
        style.map("TButton", background=[("active", "#305464"), ("pressed", "#18303b")],
                  foreground=[("disabled", "#7d929a")])
        style.configure("Accent.TButton", background="#52d9d1", foreground="#08222a")
        style.map("Accent.TButton", background=[("active", "#85f2e8"), ("pressed", "#35aaa6")],
                  foreground=[("disabled", "#617b80")])
        style.configure("Stop.TButton", background="#8f3543", foreground="#ffffff")
        style.map("Stop.TButton", background=[("active", "#b94c5b"), ("pressed", "#722b37")])
        style.configure("TNotebook", background="#09151e", borderwidth=0, bordercolor="#29434d",
                        lightcolor="#29434d", darkcolor="#29434d")
        style.configure("TNotebook.Tab", padding=(18, 9), background="#132733", foreground="#afc7cd",
                        borderwidth=0, bordercolor="#29434d", lightcolor="#29434d", darkcolor="#29434d")
        style.map("TNotebook.Tab", background=[("selected", "#244752"), ("active", "#1a3641")],
                  foreground=[("selected", "#8af3e9")])
        style.configure("TCheckbutton", background="#09151e", foreground="#e7f4f6")
        style.map("TCheckbutton", background=[("active", "#09151e")],
                  indicatorcolor=[("selected", "#52d9d1"), ("!selected", "#203946")])
        style.configure("TEntry", fieldbackground="#162b37", foreground="#f2fbfc",
                        insertcolor="#f2fbfc", bordercolor="#355765", lightcolor="#355765",
                        darkcolor="#355765", padding=5)
        style.configure("TCombobox", fieldbackground="#162b37", foreground="#f2fbfc",
                        background="#203946", arrowcolor="#a4c3ca", padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", "#162b37")],
                  foreground=[("readonly", "#f2fbfc")])
        style.configure("Height.Horizontal.TProgressbar", troughcolor="#1a3440",
                        background="#52d9d1", bordercolor="#1a3440", lightcolor="#52d9d1",
                        darkcolor="#52d9d1", thickness=7)

        frame = ttk.Frame(self.root, padding=24)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="DESK / CONTROL", style="Brand.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="YOUR WORKSPACE  /  BLUETOOTH CONTROL", style="Caption.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 18))

        notebook = ttk.Notebook(frame)
        notebook.grid(row=2, column=0, sticky="nsew")
        controls = ttk.Frame(notebook, padding=22)
        presets = ttk.Frame(notebook, padding=22)
        reminders = ttk.Frame(notebook, padding=22)
        connection = ttk.Frame(notebook, padding=22)
        notebook.add(controls, text="Control")
        notebook.add(presets, text="Presets")
        notebook.add(reminders, text="Reminders")
        notebook.add(connection, text="Connection")

        controls.columnconfigure(0, weight=1)
        ttk.Label(controls, text="HEIGHT  /  LIVE", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.current_height, style="Height.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Button(controls, text="Refresh", command=self.read_height).grid(row=1, column=1, padx=20)
        ttk.Label(controls, text="Updates while this window is open.", style="Caption.TLabel").grid(
            row=0, column=1, sticky="w")
        ttk.Progressbar(controls, variable=self.height_progress, maximum=100,
                        style="Height.Horizontal.TProgressbar").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        ttk.Label(controls, text=f"RANGE  {self.config.base_height_mm / 10:g} - "
                  f"{self.config.max_height_mm / 10:g} cm", style="Caption.TLabel").grid(
            row=3, column=0, sticky="w")
        ttk.Label(controls, text="QUICK PRESETS", style="Section.TLabel").grid(
            row=4, column=0, sticky="w", pady=(16, 4))
        self.quick_presets = ttk.Frame(controls)
        self.quick_presets.grid(row=5, column=0, columnspan=3, sticky="w")
        move_row = ttk.Frame(controls)
        move_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=(16, 8))
        ttk.Button(move_row, text="Down 2 cm", command=lambda: self.nudge(-2)).pack(side="left", padx=(0, 8))
        ttk.Button(move_row, text="Up 2 cm", command=lambda: self.nudge(2)).pack(side="left", padx=(0, 8))
        ttk.Button(move_row, text="STOP", command=self.stop, style="Stop.TButton").pack(side="left")
        target_row = ttk.Frame(controls)
        target_row.grid(row=7, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(target_row, text="Go to height (cm)").pack(side="left", padx=(0, 8))
        ttk.Entry(target_row, textvariable=self.height, width=10).pack(side="left")
        ttk.Button(target_row, text="Go", command=self.go_height, style="Accent.TButton").pack(
            side="left", padx=8)

        ttk.Label(presets, text="Manage your preferred heights", style="Title.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 15))
        ttk.Label(presets, text="Preset name").grid(row=1, column=0, sticky="w")
        ttk.Label(presets, text="Height (cm)").grid(row=1, column=2, sticky="w")
        self.preset_list = ttk.Combobox(presets, textvariable=self.preset, width=20)
        self.preset_list.grid(row=2, column=0, columnspan=2, sticky="w", pady=6)
        self.preset_list.bind("<<ComboboxSelected>>", self._preset_selected)
        ttk.Entry(presets, textvariable=self.preset_height, width=12).grid(row=2, column=2, sticky="w", padx=8)
        preset_buttons = ttk.Frame(presets)
        preset_buttons.grid(row=3, column=0, columnspan=4, sticky="w", pady=12)
        ttk.Button(preset_buttons, text="Go to preset", command=self.go_preset, style="Accent.TButton").pack(
            side="left", padx=(0, 6))
        ttk.Button(preset_buttons, text="Set / add", command=self.set_preset).pack(side="left", padx=6)
        ttk.Button(preset_buttons, text="Save current", command=self.save_current).pack(side="left", padx=6)
        ttk.Button(preset_buttons, text="Remove", command=self.remove_preset).pack(side="left", padx=6)

        ttk.Label(reminders, text="Stand and sit reminders", style="Title.TLabel").grid(
            row=0, column=0, columnspan=8, sticky="w")
        ttk.Label(reminders, text="Reminders ask before moving; they never move the desk automatically.",
                  style="Caption.TLabel").grid(row=1, column=0, columnspan=8, sticky="w", pady=(0, 12))
        ttk.Checkbutton(reminders, text="Enabled", variable=self.enabled,
                        command=self.save_days).grid(row=2, column=0, sticky="w")
        for i, day in enumerate(WEEKDAYS):
            ttk.Checkbutton(reminders, text=day.capitalize(), variable=self.days[day],
                            command=self.save_days).grid(row=2, column=i + 1)
        ttk.Label(reminders, text="Time (HH:MM)").grid(row=3, column=0, pady=12)
        ttk.Entry(reminders, textvariable=self.reminder_time, width=8).grid(row=3, column=1)
        self.reminder_preset_list = ttk.Combobox(reminders, textvariable=self.preset, width=12, state="readonly")
        self.reminder_preset_list.grid(row=3, column=2, columnspan=2, padx=4)
        self.reminder_preset_list.bind("<<ComboboxSelected>>", self._preset_selected)
        ttk.Button(reminders, text="Add reminder", command=self.add_reminder).grid(row=3, column=4, columnspan=2)
        self.reminder_list = tk.Listbox(reminders, height=4, exportselection=False,
                                        background="#162b37", foreground="#e7f4f6",
                                        selectbackground="#245363", selectforeground="#ffffff",
                                        borderwidth=0, highlightthickness=1,
                                        highlightbackground="#355765", font=("Segoe UI", 10))
        self.reminder_list.grid(row=4, column=0, columnspan=6, sticky="ew")
        ttk.Button(reminders, text="Remove", command=self.remove_reminder).grid(row=4, column=6, columnspan=2)

        ttk.Label(connection, text="Bluetooth connection", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(connection, text="Hold the desk Bluetooth button until it blinks, then scan.",
                  style="Caption.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.desk_list = ttk.Combobox(connection, textvariable=self.address, width=38)
        self.desk_list.grid(row=2, column=0, sticky="w")
        ttk.Button(connection, text="Scan", command=self.scan).grid(row=2, column=1, padx=8)
        ttk.Button(connection, text="Connect / save", command=self.setup, style="Accent.TButton").grid(
            row=2, column=2)
        ttk.Label(connection, text="Closing the window keeps the app in the notification area.\n"
                  "Use Quit in the tray menu to disconnect and exit.",
                  style="Caption.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(20, 0))

        footer = ttk.Frame(frame)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=400).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Hide to tray", command=self.hide).grid(row=0, column=1, padx=5)
        ttk.Button(footer, text="Quit", command=self.close).grid(row=0, column=2)

    def make_desk(self, address=None):
        limits = dict(base_height_mm=self.config.base_height_mm, max_height_mm=self.config.max_height_mm)
        if self.fake:
            return FakeDesk(state_file=FAKE_STATE_FILE, **limits)
        address = address or self.config.address
        if not address:
            raise ValueError("Scan and connect to a desk first.")
        return BluetoothDesk(address, **limits)

    def run(self, operation, on_success, *, takeover=False, quiet_busy=False):
        if self.closing:
            return
        if not quiet_busy:
            self.status.set("Working...")
        future = self.worker.submit(operation, takeover=takeover)
        future.add_done_callback(lambda f: self.results.put((f, on_success, quiet_busy)))

    def run_desk(self, operation, on_success, *, address=None, takeover=False, quiet_busy=False):
        selected = "fake" if self.fake else (address or self.config.address)

        async def run():
            return await self.session.run(selected, operation)

        self.run(run, on_success, takeover=takeover, quiet_busy=quiet_busy)

    def _poll_results(self):
        while True:
            try:
                action = self.tray_actions.get_nowait()
            except queue.Empty:
                break
            if action == "show":
                self.show()
            elif action == "stop":
                self.stop()
            elif action == "quit":
                self.close()
                return
        while True:
            try:
                height = self.height_updates.get_nowait()
            except queue.Empty:
                break
            self.update_height(height)
        while True:
            try:
                future, callback, quiet_busy = self.results.get_nowait()
            except queue.Empty:
                break
            try:
                value = future.result()
            except concurrent.futures.CancelledError:
                continue
            except BusyError as error:
                if quiet_busy:
                    continue
                self.status.set(f"Error: {error}")
                self.show()
                messagebox.showerror("Desk Controller", str(error), parent=self.root)
            except Exception as error:
                if quiet_busy:
                    self.live_height_enabled = False
                self.update_height(None)
                suffix = " Select Refresh to retry." if quiet_busy else ""
                self.status.set(f"Error: {error}.{suffix}")
                self.show()
                messagebox.showerror("Desk Controller", str(error), parent=self.root)
            else:
                try:
                    callback(value)
                except (OSError, ValueError) as error:
                    self.status.set(f"Error: {error}")
                    self.show()
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
        self.live_height_enabled = True

        async def connect(desk):
            return await desk.height_mm()

        def saved(height):
            self.config.address = address
            self.config.save(CONFIG_FILE)
            self.address.set(address)
            self.update_height(height)
            self.status.set(f"Connected and saved. Height: {height / 10:.1f} cm")

        self.run_desk(connect, saved, address=address)

    def read_height(self):
        self.live_height_enabled = True
        async def read(desk):
            return await desk.height_mm()
        self.run_desk(read, lambda height: self.show_height(height, "Height"))

    def update_height(self, height):
        if height is None:
            self.current_height.set("--.- cm")
            self.height_progress.set(0)
            return
        self.current_height.set(f"{height / 10:.1f} cm")
        span = self.config.max_height_mm - self.config.base_height_mm
        fraction = (height - self.config.base_height_mm) / span if span > 0 else 0
        self.height_progress.set(max(0, min(100, 100 * fraction)))

    def show_height(self, height, label):
        self.update_height(height)
        self.status.set(f"{label}: {height / 10:.1f} cm")

    def _refresh_live_height(self):
        if self.closing:
            return
        configured = self.fake or (
            self.config.address is not None and DESK_ADDRESS.fullmatch(self.config.address)
        )
        if self.live_height_enabled and self.root.winfo_viewable() and configured:
            async def read(desk):
                return await desk.height_mm()

            self.run_desk(read, self.update_height, quiet_busy=True)
        self.root.after(2000, self._refresh_live_height)

    def move(self, target):
        async def go(desk):
            return await desk.move_to(target * 10, on_state=lambda state: self.height_updates.put(state.height_mm))
        self.run_desk(go, lambda height: self.show_height(height, "Desk at"), takeover=True)

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
            return await desk.move_to(target, on_state=lambda state: self.height_updates.put(state.height_mm))
        self.run_desk(move, lambda height: self.show_height(height, "Desk at"), takeover=True)

    def stop(self):
        async def stop_desk(desk):
            await desk.stop()
            return await desk.height_mm()
        self.run_desk(stop_desk, lambda height: self.show_height(height, "Stopped at"), takeover=True)

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
        self.reminder_preset_list["values"] = list(self.config.presets)
        for child in self.quick_presets.winfo_children():
            child.destroy()
        for index, (name, height) in enumerate(self.config.presets.items()):
            ttk.Button(self.quick_presets, text=f"{name.capitalize()}  {height:g} cm",
                       command=lambda target=height: self.move(target)).grid(
                row=index // 3, column=index % 3, padx=(0, 8), pady=4, sticky="w")
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

    def hide(self):
        if not self.closing:
            self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.show()
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
            self.tray.stop()
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
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("Desk Controller", f"Could not load settings: {error}", parent=root)
            root.destroy()
            return 1
        root.mainloop()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

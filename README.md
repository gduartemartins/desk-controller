# desk-controller

Control an IKEA IDÅSEN (LINAK DPG controller) standing desk over Bluetooth LE:
go straight to a height instead of holding the button.

The repository contains a Windows desktop app and a Linux CLI / Omarchy add-on.
Both use the same Bluetooth protocol and movement logic.

## Windows desktop app

### Install and start

Install Python 3.14 with Tkinter and enable Bluetooth on Windows. From the repository
directory, install the app and its dependencies in PowerShell:

```powershell
py -3.14 -m pip install .
py -3.14 -m desk_controller.windows
```

If `py` is not available, use the path to your Python 3.14 installation instead, for
example:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m pip install .
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m desk_controller.windows
```

The install also provides `desk-windows` when Python's Scripts directory is on PATH.
If an organisation's security policy blocks the launcher or Python, consult your IT
team rather than changing the policy.

### Connect and control

1. Disconnect the IKEA phone app and any other device connected to the desk: the desk
   accepts one Bluetooth connection at a time.
2. Hold the desk controller's Bluetooth button until its light blinks blue. In the
   app's **Connection** tab, choose **Scan**, select the desk and click **Connect /
   save**. Accept any Windows pairing prompt.
3. Use **Control** for the current height, presets, a custom height, 2 cm nudges and
   **STOP**. The height refreshes while the window is open and during app-initiated
   movement. Use **Presets** to add, edit or remove a preset or save the current height.

The app tries to retain its Bluetooth connection while running and reconnects when
needed. A new move or stop cancels an active move. Check the desk's surroundings
before moving it and keep the physical controls accessible; a software stop cannot
be guaranteed if the Bluetooth connection fails.

### Tray and reminders

Closing the window or choosing **Hide to tray** leaves the app running in the Windows
notification area. Click its icon to reopen the window; right-click it for **Stop desk**
or **Quit**. Quitting disconnects the desk and stops reminders. The app does not start
automatically when Windows starts.

In **Reminders**, enable reminders, select days, a time and a preset, and choose
**Add reminder**. Each reminder offers **Move now**, snooze for 10 or 30 minutes, or
skip; the desk never moves automatically. Reminders and snoozes work while the app
is running, including when hidden in the tray, but do not survive quitting the app.

Windows settings and simulated state are stored in `%LOCALAPPDATA%\desk-controller\`.
To adjust height calibration or limits, quit the app and edit `base_height_mm` or
`max_height_mm` in `config.toml` (values are in millimetres).

### Try the simulator

```powershell
py -3.14 -m desk_controller.windows --fake
```

The simulator does not control a physical desk. It shares the Windows settings file:
using **Connect / save** in simulator mode saves `fake` as the selected address, so
scan and save the real desk again when switching back.

## Linux CLI

```sh
desk stand            # go to a preset
desk 105              # go to 105 cm
desk up / desk down 5 # nudge (default 2 cm)
desk stop
desk status
desk save focus       # save the current height as a preset
desk presets
```

Pressing a new command while the desk is moving cancels the old one.

## Omarchy add-on

`omarchy/install.sh` adds a **Desk** entry to the Omarchy menu (also `SUPER+CTRL+HOME`,
or `desk menu`):

- **Presets**: go to sit / stand (or a third one like *focus*); set a preset's height by
  typing it, save the desk's current height as a preset, add or remove presets.
- **Go to height…**: type any height in cm.
- **Reminders**: times of day to stand or sit, on chosen days. At each time a notification
  appears; click it to move the desk, snooze 10/30 min, or skip. The desk never moves on
  its own. Reminders run in the `desk-reminders` systemd user service.

`desk schedule` prints the reminder schedule.
Config lives in `~/.config/desk-controller/config.toml`.

Hyprland keybindings (in `~/.config/hypr/bindings.lua`):

| Keys | Action |
|---|---|
| `SUPER+CTRL+UP` / `DOWN` | stand / sit |
| `SUPER+CTRL+ALT+UP` / `DOWN` | nudge 2 cm |
| `SUPER+CTRL+END` | stop |
| `SUPER+CTRL+HOME` | desk menu |

## Bluetooth notes

The Linux version has been used with a real desk ("Desk 9160") through a TP-Link UB500
adapter, and the Windows app has also been used with a physical desk. If another device has
the only connection, the desk may not appear in scans. On Linux, if BlueZ already holds
the connection (for example via the Omarchy Bluetooth panel), the CLI releases it.

## Next actions

1. **Check height calibration**: the IKEA app shows 1 cm more than `desk` (83 vs 82).
   Measure the desktop; if the app is right, set `base_height_mm = 630` in the config.
2. **Set real presets** from the Desk menu, and turn reminders on.
3. **`desk serve` daemon**: keep one Bluetooth connection open and expose an HTTP API,
   so the desk can be controlled from other devices.
4. **iPhone**: mobile-friendly web page served by the daemon, plus Apple Shortcuts.
5. **Home Assistant**: REST/MQTT integration against the daemon.
6. **Standing schedule**: stand at set times during the day, with a warning and snooze.

Details in [ROADMAP.md](ROADMAP.md).

## Linux first run with the real desk

1. Plug in the Bluetooth adapter and start Bluetooth: `sudo systemctl enable --now bluetooth`
2. Hold the Bluetooth button on the desk controller until the light blinks blue.
3. `desk scan` to confirm the desk shows up, then `desk setup` to pair and save it.
4. `desk status`, then try a small move: `desk up 2`.
5. Stand/sit at your preferred heights and `desk save stand` / `desk save sit`.

Add `-v` (or `-vv`) to any command for connection/movement logs.

## Development

Run the shared test suite on either platform with Python 3.14 and `uv`. Install the
system Tkinter package to run the Windows interface tests; GUI tests also need a
desktop session.

```sh
uv run pytest
```

On Linux:

```sh
DESK_FAKE=1 desk stand         # try the CLI without hardware
uv tool install --editable .   # (re)install `desk` into ~/.local/bin
```

`DESK_FAKE=1` stores the simulated height in `~/.local/state/desk-controller/`.
The Windows app uses `--fake` as described above; the Linux `desk` CLI and Omarchy
integration are not available on Windows.

Protocol notes are in `src/desk_controller/protocol.py` (based on
[newAM/idasen](https://github.com/newAM/idasen)). See [ROADMAP.md](ROADMAP.md) for plans.

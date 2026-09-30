# desk-controller

Control an IKEA IDÅSEN (LINAK DPG controller) standing desk over Bluetooth LE:
go straight to a height instead of holding the button.

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
Config lives in `~/.config/desk-controller/config.toml`.

Hyprland keybindings (in `~/.config/hypr/bindings.lua`):

| Keys | Action |
|---|---|
| `SUPER+CTRL+UP` / `DOWN` | stand / sit |
| `SUPER+CTRL+ALT+UP` / `DOWN` | nudge 2 cm |
| `SUPER+CTRL+END` | stop |

## Status

Built and tested against a simulated desk; not yet tried on real hardware
(this PC needs a Bluetooth adapter, a TP-Link UB500 is on its way).

## Next actions

1. **Verify on the real desk**: pair, check height reading and movement (steps below).
   If something doesn't respond, compare `desk -vv ...` logs with the protocol notes.
2. **Set real presets**: `desk save sit` / `desk save stand` at comfortable heights.
3. **`desk serve` daemon**: keep one Bluetooth connection open and expose an HTTP API,
   so the desk can be controlled from other devices.
4. **iPhone / Windows**: mobile-friendly web page served by the daemon, plus Apple Shortcuts.
5. **Home Assistant**: REST/MQTT integration against the daemon.
6. **Standing schedule**: stand at set times during the day, with a warning and snooze.

Details in [ROADMAP.md](ROADMAP.md).

## First run with the real desk

1. Plug in the Bluetooth adapter and start Bluetooth: `sudo systemctl enable --now bluetooth`
2. Hold the Bluetooth button on the desk controller until the light blinks blue.
3. `desk scan` to confirm the desk shows up, then `desk setup` to pair and save it.
4. `desk status`, then try a small move: `desk up 2`.
5. Stand/sit at your preferred heights and `desk save stand` / `desk save sit`.

Add `-v` (or `-vv`) to any command for connection/movement logs.

## Development

```sh
uv run pytest                  # tests (use the simulated desk)
DESK_FAKE=1 desk stand         # try the CLI without hardware
uv tool install --editable .   # (re)install `desk` into ~/.local/bin
```

`DESK_FAKE=1` stores the simulated height in `~/.local/state/desk-controller/`.

Protocol notes are in `src/desk_controller/protocol.py` (based on
[newAM/idasen](https://github.com/newAM/idasen)). See [ROADMAP.md](ROADMAP.md) for plans.

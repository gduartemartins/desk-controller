# Roadmap

## Now: Omarchy / CLI  ✅
- [x] Bluetooth protocol + `desk` CLI (presets, go-to-height, nudge, stop)
- [x] Simulated desk (`--fake`) and tests
- [x] Hyprland keybindings (`SUPER+CTRL+UP/DOWN` etc., in `~/.config/hypr/bindings.lua`)
- [x] Verified against the real desk (TP-Link UB500)
- [x] Omarchy add-on: Desk menu (presets, custom height, reminders), `SUPER+CTRL+HOME`
- [x] Stand/sit reminders (`desk-reminders` user service, click to move / snooze / skip)

## Next: control from anywhere
Wanted: use the desk from **iPhone**, **Windows**, and **Home Assistant**.

The desk accepts only one Bluetooth connection at a time, so everything should go
through a single always-running service instead of each device connecting itself:

- `desk serve`: daemon on this PC that keeps the Bluetooth connection open
  (faster moves, live height) and exposes a small HTTP API on the LAN
  (`GET /height`, `POST /move {"target": "stand"}`, `POST /stop`).
- The CLI and keybindings talk to the daemon when it's running.
- **iPhone**: a mobile-friendly web page served by the daemon (add to home screen),
  and/or Apple Shortcuts calling the API (lets Siri say "stand up").
- **Windows**: same web page in a browser; no install needed.
- **Home Assistant**: either
  - REST/MQTT integration pointing at the daemon (keeps this PC as the Bluetooth hub), or
  - HA's built-in *IKEA Idasen Desk* integration, if HA gets its own Bluetooth
    (adapter or ESPHome Bluetooth proxy near the desk). Only one of HA or this PC
    can be connected at a time.
- Optional: an ESP32 next to the desk as the Bluetooth bridge, so it works while this PC is off.

## Standing schedule
Basic reminders are done (see README). Ideas for later, e.g. in `config.toml`:

```toml
[schedule]
days = ["mon", "tue", "wed", "thu", "fri"]
stand = ["10:00", "14:30", "16:30"]
duration_min = 30          # sit back down afterwards
warn_before_s = 30         # notification with "snooze"/"skip" before moving
skip_when_idle = true      # don't move if the screen is locked / nobody's there
```

- Runs inside `desk serve` (or a systemd user timer as a first step).
- Always notify before moving; never move an unattended desk.
- Later: track sit/stand time per day.

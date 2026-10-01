#!/bin/bash
# Install the desk add-on for Omarchy: the `desk` command, a "Desk" entry in the
# Omarchy menu, a SUPER+CTRL+HOME keybinding, and the stand/sit reminder service.
# Safe to run again; it only adds what's missing.

set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
desk="$HOME/.local/bin/desk"
menu="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
bindings="$HOME/.config/hypr/bindings.lua"
units="$HOME/.config/systemd/user"

echo "Installing the desk command"
uv tool install --quiet --editable "$repo" --force

if [[ -f $menu ]] && ! grep -q '"desk":' "$menu"; then
  echo "Adding Desk to the Omarchy menu"
  entry="  \"desk\": {\"icon\":\"󱈹\",\"label\":\"Desk\",\"aliases\":[\"standing desk\",\"stand\",\"sit\"],\"description\":\"Presets, custom height, stand/sit reminders\",\"action\":\"$desk menu\"},"
  # Insert before the closing brace (the parser allows trailing commas).
  last=$(grep -n '^}' "$menu" | tail -1 | cut -d: -f1)
  sed -i "${last}i\\$entry" "$menu"
fi

if [[ -f $bindings ]] && ! grep -q 'desk menu' "$bindings"; then
  echo "Adding SUPER+CTRL+HOME keybinding"
  printf 'o.bind("SUPER + CTRL + HOME", "Desk: menu", "%s menu")\n' "$desk" >>"$bindings"
  hyprctl reload >/dev/null || true
fi

echo "Enabling the reminder service"
mkdir -p "$units"
cp "$repo/omarchy/desk-reminders.service" "$units/"
systemctl --user daemon-reload
systemctl --user enable --now desk-reminders.service
systemctl --user restart desk-reminders.service

echo "Done. Open it from the Omarchy menu (Desk) or SUPER+CTRL+HOME."

"""User config at ~/.config/desk-controller/config.toml.

Presets are stored in centimetres, matching what the desk's own app shows.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import protocol


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "desk-controller"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "desk-controller"
CONFIG_FILE = CONFIG_DIR / "config.toml"
FAKE_STATE_FILE = STATE_DIR / "fake-desk.json"


@dataclass
class Config:
    address: str | None = None
    base_height_mm: float = protocol.DEFAULT_BASE_HEIGHT_MM
    max_height_mm: float = protocol.DEFAULT_MAX_HEIGHT_MM
    presets: dict[str, float] = field(default_factory=lambda: {"sit": 73.0, "stand": 110.0})

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "Config":
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text())
        config = cls()
        config.address = data.get("address", config.address)
        config.base_height_mm = float(data.get("base_height_mm", config.base_height_mm))
        config.max_height_mm = float(data.get("max_height_mm", config.max_height_mm))
        if "presets" in data:
            config.presets = {name: float(cm) for name, cm in data["presets"].items()}
        return config

    def save(self, path: Path = CONFIG_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if self.address:
            lines.append(f'address = "{self.address}"')
        lines += [
            f"base_height_mm = {self.base_height_mm:g}",
            f"max_height_mm = {self.max_height_mm:g}",
            "",
            "# Heights in cm. Use `desk save <name>` to store the current height.",
            "[presets]",
            *(f"{name} = {cm:g}" for name, cm in self.presets.items()),
        ]
        path.write_text("\n".join(lines) + "\n")

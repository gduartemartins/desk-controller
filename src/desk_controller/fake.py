"""A simulated desk that speaks the same bytes as the real controller.

Used for development without hardware (`desk --fake ...`) and in tests. It
mimics the firmware behaviour that matters: movement only continues while the
target/command is refreshed, speed is reported while moving, and an optional
obstacle (a ceiling the desk can't rise past) makes it stall.
"""

import json
import struct
import time
from pathlib import Path

from . import protocol
from .desk import Desk

# How long the real controller keeps moving without a refreshed target.
KEEPALIVE_S = 1.0


class FakeDesk(Desk):
    def __init__(
        self,
        *,
        height_mm: float = 720.0,
        speed_mm_s: float = 38.0,
        obstacle_mm: float | None = None,
        state_file: Path | None = None,
        clock=time.monotonic,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.position_mm = height_mm
        self.speed_mm_s = speed_mm_s
        self.obstacle_mm = obstacle_mm
        self.state_file = state_file
        self.clock = clock
        self.connected = False
        self.user_registered = False
        self._target: float | None = None
        self._expires = 0.0
        self._velocity = 0.0
        self._last = clock()

    async def connect(self) -> None:
        if self.state_file and self.state_file.exists():
            self.position_mm = json.loads(self.state_file.read_text())["height_mm"]
        self._last = self.clock()
        self.connected = True

    async def disconnect(self) -> None:
        self._advance()
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"height_mm": self.position_mm}))
        self.connected = False

    def _advance(self) -> None:
        now = self.clock()
        # Movement only happens up to the keepalive expiry, even if we look later.
        dt = max(0.0, min(now, self._expires) - self._last)
        self._last = now
        if self._target is None or dt == 0:
            self._target, self._velocity = None, 0.0
            return

        direction = 1 if self._target > self.position_mm else -1
        new = self.position_mm + direction * self.speed_mm_s * dt
        if direction * (new - self._target) >= 0:
            new = self._target
        if self.obstacle_mm is not None and new > self.obstacle_mm >= self.position_mm:
            new = self.obstacle_mm  # something is blocking the desk from rising further
        new = min(max(new, self.base_height_mm), self.max_height_mm)

        moved = new != self.position_mm
        self.position_mm = new
        self._velocity = direction * self.speed_mm_s if moved else 0.0
        if new == self._target:
            self._target = None

    def _move_towards(self, target_mm: float) -> None:
        self._advance()
        self._target = target_mm
        self._expires = self.clock() + KEEPALIVE_S

    async def _write_command(self, data: bytes) -> None:
        if data == protocol.COMMAND_UP:
            self._move_towards(self.max_height_mm)
        elif data == protocol.COMMAND_DOWN:
            self._move_towards(self.base_height_mm)
        elif data == protocol.COMMAND_STOP:
            self._advance()
            self._target, self._velocity = None, 0.0

    async def _write_reference(self, data: bytes) -> None:
        if data == protocol.REFERENCE_INPUT_STOP:
            self._advance()
            self._target, self._velocity = None, 0.0
            return
        (raw,) = struct.unpack("<H", data)
        self._move_towards(self.base_height_mm + raw / 10)

    async def _write_dpg(self, data: bytes) -> None:
        if data.startswith(b"\x7f\x86\x80"):
            self.user_registered = True

    async def _read_height(self) -> bytes:
        self._advance()
        position = round((self.position_mm - self.base_height_mm) * 10)
        return struct.pack("<Hh", position, round(self._velocity * 10))

"""LINAK DPG Bluetooth LE protocol, as used by the IKEA IDÅSEN desk.

Reference: https://github.com/newAM/idasen

Heights on the wire are unsigned 0.1 mm steps *above the desk's lowest
position*. Everything outside this module works in absolute millimetres.
"""

import struct

UUID_ADV_SVC = "99fa0001-338a-1024-8a49-009c0215f78a"
UUID_COMMAND = "99fa0002-338a-1024-8a49-009c0215f78a"
UUID_DPG = "99fa0011-338a-1024-8a49-009c0215f78a"
UUID_HEIGHT = "99fa0021-338a-1024-8a49-009c0215f78a"
UUID_REFERENCE_INPUT = "99fa0031-338a-1024-8a49-009c0215f78a"

COMMAND_UP = bytes([0x47, 0x00])
COMMAND_DOWN = bytes([0x46, 0x00])
COMMAND_STOP = bytes([0xFF, 0x00])
COMMAND_WAKEUP = bytes([0xFE, 0x00])
REFERENCE_INPUT_STOP = bytes([0x01, 0x80])

# Registers this client as a DPG "user"; some desks ignore movement until it's sent.
DPG_USER_QUERY = b"\x7f\x86\x00"
DPG_USER_REGISTER = b"\x7f\x86\x80" + bytes(range(1, 0x12))

# IDÅSEN physical range. Other LINAK desks may differ; see config.
DEFAULT_BASE_HEIGHT_MM = 620.0
DEFAULT_MAX_HEIGHT_MM = 1270.0


def decode_height(raw: bytes, base_mm: float = DEFAULT_BASE_HEIGHT_MM) -> tuple[float, float]:
    """Decode the height characteristic into (height_mm, speed_mm_per_s)."""
    if len(raw) != 4:
        raise ValueError(f"expected 4 bytes, got {len(raw)}")
    position, speed = struct.unpack("<Hh", raw)
    return base_mm + position / 10, speed / 10


def encode_height(height_mm: float, base_mm: float = DEFAULT_BASE_HEIGHT_MM) -> bytes:
    """Encode an absolute height as a reference-input target."""
    return struct.pack("<H", round((height_mm - base_mm) * 10))

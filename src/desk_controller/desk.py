"""Desk connections: the shared movement logic plus the real Bluetooth backend."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from . import protocol

log = logging.getLogger(__name__)

# The desk lands within a couple of mm of the target; this is how close counts as "there".
TOLERANCE_MM = 5.0


class DeskError(Exception):
    pass


class DeskStalled(DeskError):
    """The desk stopped before reaching the target (obstacle, anti-collision, or limit)."""


@dataclass
class DeskState:
    height_mm: float
    speed_mm_s: float


class Desk(ABC):
    """Movement logic shared by every backend.

    Backends only implement the four raw GATT operations below, so the real desk
    and the simulator run exactly the same move_to() code.
    """

    def __init__(
        self,
        base_height_mm: float = protocol.DEFAULT_BASE_HEIGHT_MM,
        max_height_mm: float = protocol.DEFAULT_MAX_HEIGHT_MM,
    ):
        self.base_height_mm = base_height_mm
        self.max_height_mm = max_height_mm

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def _write_command(self, data: bytes) -> None: ...

    @abstractmethod
    async def _write_reference(self, data: bytes) -> None: ...

    @abstractmethod
    async def _write_dpg(self, data: bytes) -> None: ...

    @abstractmethod
    async def _read_height(self) -> bytes: ...

    async def __aenter__(self) -> "Desk":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def state(self) -> DeskState:
        height, speed = protocol.decode_height(await self._read_height(), self.base_height_mm)
        return DeskState(height, speed)

    async def height_mm(self) -> float:
        return (await self.state()).height_mm

    async def register_user(self) -> None:
        await self._write_dpg(protocol.DPG_USER_QUERY)
        await self._write_dpg(protocol.DPG_USER_REGISTER)

    async def wakeup(self) -> None:
        await self._write_command(protocol.COMMAND_WAKEUP)

    async def stop(self) -> None:
        await self._write_command(protocol.COMMAND_STOP)
        await self._write_reference(protocol.REFERENCE_INPUT_STOP)

    async def move_to(
        self,
        target_mm: float,
        *,
        poll_interval: float = 0.2,
        stall_polls: int = 5,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> float:
        """Drive the desk to target_mm and return the height it settled at.

        The desk only keeps moving while the target is re-sent, so we refresh it
        every poll. Any exit other than arriving (error, timeout, cancellation)
        sends an explicit stop.
        """
        if not self.base_height_mm <= target_mm <= self.max_height_mm:
            raise ValueError(
                f"{target_mm / 10:.1f} cm is outside the desk's range "
                f"({self.base_height_mm / 10:.1f}-{self.max_height_mm / 10:.1f} cm)"
            )

        payload = protocol.encode_height(target_mm, self.base_height_mm)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        still = 0
        retries = 0
        arrived = False

        await self.wakeup()
        await self._write_command(protocol.COMMAND_STOP)
        try:
            while True:
                await self._write_reference(payload)
                await asyncio.sleep(poll_interval)
                state = await self.state()
                log.debug("height=%.1fmm speed=%.1fmm/s", state.height_mm, state.speed_mm_s)

                if state.speed_mm_s != 0:
                    still = 0
                    continue
                if abs(state.height_mm - target_mm) <= TOLERANCE_MM:
                    arrived = True
                    return state.height_mm

                still += 1
                if still >= stall_polls:
                    retries += 1
                    if retries > max_retries:
                        raise DeskStalled(
                            f"desk stopped at {state.height_mm / 10:.1f} cm "
                            f"before reaching {target_mm / 10:.1f} cm"
                        )
                    log.info("desk not moving, retrying (%d/%d)", retries, max_retries)
                    still = 0
                    await self.wakeup()
                if loop.time() > deadline:
                    raise DeskError(f"timed out after {timeout:.0f}s")
        finally:
            if not arrived:
                await asyncio.shield(self._safe_stop())

    async def _safe_stop(self) -> None:
        try:
            await self.stop()
        except Exception:
            log.exception("failed to stop desk")


@dataclass
class FoundDesk:
    address: str
    name: str
    rssi: int


async def scan(timeout: float = 8.0) -> list[FoundDesk]:
    """Find LINAK desks advertising nearby."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    desks = []
    for device, adv in found.values():
        name = device.name or adv.local_name or ""
        if protocol.UUID_ADV_SVC in adv.service_uuids or name.startswith("Desk"):
            desks.append(FoundDesk(device.address, name, adv.rssi))
    return sorted(desks, key=lambda d: d.rssi, reverse=True)


async def bluetoothctl(*args: str, timeout: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise DeskError(f"bluetoothctl {args[0]} timed out") from None
    return out.decode(errors="replace")


async def bluez_connected(address: str) -> bool:
    try:
        return "Connected: yes" in await bluetoothctl("info", address, timeout=5)
    except (OSError, DeskError):
        return False


async def pair(address: str) -> None:
    """Pair through bluetoothctl, which brings its own agent (bleak's pair=True needs one running)."""
    if "Paired: yes" in await bluetoothctl("info", address, timeout=5):
        return
    out = await bluetoothctl("--agent", "NoInputNoOutput", "pair", address, timeout=35)
    if "Pairing successful" not in out and "AlreadyExists" not in out:
        last = out.strip().splitlines()[-1] if out.strip() else "no output"
        raise DeskError(
            f"Pairing failed ({last}). Hold the Bluetooth button on the desk until "
            "the light blinks blue, then run `desk setup` again."
        )


class BluetoothDesk(Desk):
    def __init__(self, address: str, *, connect_attempts: int = 3, **kwargs):
        super().__init__(**kwargs)
        self.address = address
        self.connect_attempts = connect_attempts
        self._client: BleakClient | None = None

    async def connect(self) -> None:
        # A desk that's connected doesn't advertise, so bleak can't find it. If the
        # connection belongs to this PC (BlueZ reconnecting it, or the Omarchy
        # Bluetooth panel), drop it so we can make our own.
        if await bluez_connected(self.address):
            log.info("BlueZ already holds a connection to the desk, releasing it")
            await bluetoothctl("disconnect", self.address)
            await asyncio.sleep(1)
        last_error: Exception | None = None
        for attempt in range(1, self.connect_attempts + 1):
            # pair=True lets BlueZ pair on first use; it's a no-op once bonded.
            client = BleakClient(self.address, pair=True, timeout=10)
            try:
                await client.connect()
            except (BleakError, TimeoutError) as e:
                last_error = e
                log.info("connect attempt %d failed: %s", attempt, e)
                await asyncio.sleep(1)
                continue
            self._client = client
            try:
                await self.register_user()
            except BleakError as e:
                log.debug("DPG user registration failed (usually harmless): %s", e)
            return
        if "not found" in str(last_error):
            raise DeskError(
                "Desk not found. Is another device connected to it (e.g. the IKEA app "
                "on a phone)? The desk only accepts one connection at a time."
            )
        raise DeskError(f"could not connect to {self.address}: {last_error}")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None

    @property
    def client(self) -> BleakClient:
        if not self._client:
            raise DeskError("not connected")
        return self._client

    async def _write_command(self, data: bytes) -> None:
        await self.client.write_gatt_char(protocol.UUID_COMMAND, data)

    async def _write_reference(self, data: bytes) -> None:
        await self.client.write_gatt_char(protocol.UUID_REFERENCE_INPUT, data)

    async def _write_dpg(self, data: bytes) -> None:
        await self.client.write_gatt_char(protocol.UUID_DPG, data)

    async def _read_height(self) -> bytes:
        return bytes(await self.client.read_gatt_char(protocol.UUID_HEIGHT))

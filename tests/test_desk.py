import asyncio

import pytest

from desk_controller import protocol
from desk_controller.cli import normalize_argv, resolve_target, UserError
from desk_controller.config import Config
from desk_controller.desk import DeskStalled
from desk_controller.fake import FakeDesk

FAST = dict(poll_interval=0.01, stall_polls=3)


def fast_desk(**kwargs) -> FakeDesk:
    return FakeDesk(speed_mm_s=2000, **kwargs)


def test_height_roundtrip():
    raw = protocol.encode_height(1100.0)
    assert raw == (4800).to_bytes(2, "little")
    assert protocol.decode_height(raw + b"\x00\x00") == (1100.0, 0.0)


def test_decode_negative_speed():
    height, speed = protocol.decode_height(bytes([0x10, 0x27, 0x9C, 0xFE]))
    assert height == pytest.approx(620 + 1000)
    assert speed == pytest.approx(-35.6)


def test_decode_rejects_wrong_length():
    with pytest.raises(ValueError):
        protocol.decode_height(b"\x00\x00")


@pytest.mark.parametrize("start, target", [(720, 1100), (1100, 730), (900, 900)])
async def test_move_to_arrives(start, target):
    async with fast_desk(height_mm=start) as desk:
        landed = await desk.move_to(target, **FAST)
        assert landed == pytest.approx(target, abs=1)


async def test_move_to_out_of_range():
    async with fast_desk() as desk:
        with pytest.raises(ValueError):
            await desk.move_to(1500, **FAST)


async def test_move_to_stalls_on_obstacle_and_stops():
    async with fast_desk(height_mm=720, obstacle_mm=900) as desk:
        with pytest.raises(DeskStalled):
            await desk.move_to(1100, max_retries=1, **FAST)
        assert desk.position_mm == pytest.approx(900)
        assert (await desk.state()).speed_mm_s == 0


async def test_cancel_stops_desk():
    desk = FakeDesk(height_mm=720, speed_mm_s=100)
    async with desk:
        task = asyncio.create_task(desk.move_to(1200, poll_interval=0.01))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        stopped_at = desk.position_mm
        await asyncio.sleep(0.1)
        assert (await desk.height_mm()) == pytest.approx(stopped_at, abs=0.1)
        assert 720 < stopped_at < 1200


async def test_desk_stops_without_keepalive():
    """Like the real controller, one reference write alone doesn't finish a long move."""
    desk = FakeDesk(height_mm=720, speed_mm_s=100)
    async with desk:
        await desk._write_reference(protocol.encode_height(1200))
        await asyncio.sleep(1.3)
        height = await desk.height_mm()
        assert 750 < height < 900


async def test_fake_persists_height(tmp_path):
    state = tmp_path / "fake.json"
    async with fast_desk(state_file=state) as desk:
        await desk.move_to(1050, **FAST)
    async with FakeDesk(state_file=state) as desk:
        assert await desk.height_mm() == pytest.approx(1050, abs=1)


def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    config = Config(address="AA:BB:CC:DD:EE:FF")
    config.presets["focus"] = 104.5
    config.save(path)
    loaded = Config.load(path)
    assert loaded == config


def test_resolve_target():
    config = Config()
    assert resolve_target("stand", config) == 1100
    assert resolve_target("95.5", config) == 955
    with pytest.raises(UserError):
        resolve_target("lunch", config)


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["stand"], ["go", "stand"]),
        (["--notify", "105"], ["--notify", "go", "105"]),
        (["up", "3"], ["up", "3"]),
        (["status"], ["status"]),
    ],
)
def test_shorthand(argv, expected):
    assert normalize_argv(argv) == expected

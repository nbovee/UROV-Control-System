"""Tests for input normalization, the input mapper, and evdev discovery."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from config import load_config
from controller import controller as ctl
from tests.fakes import FakeAbsInfo, FakeEvdev, FakeEvent, FakeInputDevice

F310, F710 = "Logitech Gamepad F310", "Logitech Gamepad F710"
CONFIG = load_config(Path(__file__).parents[1] / "configs" / "default.toml")


@pytest.fixture
def mapper() -> ctl.InputMapper:
    return ctl.InputMapper(CONFIG)


def collect_events(controller: ctl.Controller) -> list:
    async def collect():
        return [event async for event in controller.events()]

    return asyncio.run(collect())


@pytest.mark.parametrize(
    ("value", "minimum", "maximum", "expected"),
    [
        (0, -32768, 32767, 0.0), (32767, -32768, 32767, 1.0), (-32768, -32768, 32767, -1.0),
        (16384, -32768, 32767, 0.5), (-16384, -32768, 32767, -0.5), (0, 0, 255, 0.0),
        (255, 0, 255, 1.0), (128, 0, 255, 128 / 255), (50000, -32768, 32767, 1.0),
        (-50000, -32768, 32767, -1.0), (-50, 0, 255, 0.0), (99999, 0, 255, 1.0),
        (10, 5, 5, 0.0),
    ],
)
def test_normalize_axis(value, minimum, maximum, expected) -> None:
    assert ctl.normalize_axis(value, minimum, maximum) == pytest.approx(expected, abs=1e-4)


@pytest.mark.parametrize(
    ("value", "deadzone", "expected"),
    [
        (0.0, 0.1, 0.0), (0.05, 0.1, 0.0), (-0.09, 0.1, 0.0), (0.55, 0.1, 0.5),
        (-0.55, 0.1, -0.5), (1.0, 0.1, 1.0), (0.5, 0.0, 0.5), (-2.0, 0.0, -1.0),
    ],
)
def test_apply_deadzone(value, deadzone, expected) -> None:
    assert ctl.apply_deadzone(value, deadzone) == pytest.approx(expected)


def test_input_mapper_lifecycle(mapper) -> None:
    assert mapper.armed is False
    mapper.process_event(ctl.EV_ABS, 1, 32767)
    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)
    assert mapper.motor_setpoints() == {"left": 0.0, "right": 0.0, "vertical": 0.0}
    mapper.process_event(ctl.EV_KEY, 304, 1)
    assert mapper.armed is False
    mapper.reset()
    mapper.process_event(ctl.EV_ABS, 2, 255)
    mapper.process_event(ctl.EV_KEY, 304, 1)
    assert mapper.armed is False
    mapper.process_event(ctl.EV_ABS, 2, 0)
    mapper.process_event(ctl.EV_KEY, 304, 1)
    assert mapper.armed is True
    mapper.process_event(ctl.EV_ABS, 1, 32767)
    assert mapper.motor_setpoints()["left"] == pytest.approx(1.0, abs=1e-4)
    for _ in range(4):
        mapper.process_event(ctl.EV_KEY, 305, 1)
        assert mapper.armed is False
        assert all(value == 0.0 for value in mapper.motor_setpoints().values())
    mapper.process_event(ctl.EV_ABS, 2, 255)
    assert mapper.controls["vertical"] == 1.0
    mapper.process_event(ctl.EV_ABS, 5, 255)
    assert mapper.controls["vertical"] == 0.0
    mapper.process_event(ctl.EV_ABS, 5, 0)
    assert mapper.controls["vertical"] == 1.0
    mapper.process_event(ctl.EV_ABS, 2, 200)
    mapper.process_event(ctl.EV_ABS, 5, 100)
    assert mapper.controls["vertical"] == pytest.approx(100 / 255)
    assert {"L_trig", "R_trig"}.isdisjoint(mapper.controls)
    mapper.process_event(ctl.EV_ABS, 1, 32767)
    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)
    mapper.reset()
    assert all(value == 0.0 for value in mapper.controls.values())
    mapper.process_event(ctl.EV_ABS, 5, 255)
    assert mapper.controls["vertical"] == -1.0
    mapper.reset()
    mapper.process_event(ctl.EV_KEY, 304, 1)
    mapper.process_event(ctl.EV_ABS, 1, 32767)
    mapper.process_event(ctl.EV_ABS, 4, 32767)
    mapper.process_event(ctl.EV_ABS, 2, 255)
    expected = {"left": 1.0, "right": 1.0, "vertical": 1.0}
    assert mapper.motor_setpoints() == pytest.approx(expected)
    mapper.process_event(ctl.EV_KEY, 310, 1)  # L_bmp
    assert mapper.motor_setpoints() == pytest.approx({**expected, "left": -1.0})
    mapper.process_event(ctl.EV_KEY, 310, 1)  # Toggle back.
    assert mapper.motor_setpoints() == pytest.approx(expected)
    mapper.process_event(ctl.EV_KEY, 310, 1)  # Toggle left again.
    mapper.process_event(ctl.EV_KEY, 311, 1)  # R_bmp
    mapper.process_event(ctl.EV_KEY, 308, 1)  # Y
    assert mapper.motor_setpoints() == pytest.approx(
        {"left": -1.0, "right": -1.0, "vertical": -1.0}
    )
    mapper.process_event(ctl.EV_KEY, 314, 1)  # Back
    assert mapper.motor_setpoints() == pytest.approx(expected)
    mapper.reset()
    assert mapper.process_event(ctl.EV_KEY, 1, 1) is False
    assert mapper.controls["L_Y"] == 0.0
    assert mapper.process_event(ctl.EV_ABS, 1, 32767) is True
    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)
    assert mapper.process_event(ctl.EV_KEY, 1, 0) is False
    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize(
    ("names", "grab_error", "expected_index"),
    [((F310,), None, 1), ((F710,), None, 1), ((F710, F310), OSError("busy"), 2)],
)
def test_connect_discovery_grab_skip_and_disconnect(
    names: tuple[str, ...], grab_error: OSError | None, expected_index: int
) -> None:
    devices = [FakeInputDevice("/dev/input/event0", "Keyboard")]
    paths = ("/dev/input/event1", "/dev/input/event2")
    devices += [FakeInputDevice(paths[i], name) for i, name in enumerate(names)]
    devices[1].grab_error = grab_error
    expected = devices[expected_index]
    expected.absinfo_map[1] = FakeAbsInfo(-32768, 32767)
    controller = ctl.Controller(CONFIG.controller, evdev_mod=FakeEvdev(devices))
    assert controller.connect() is True and controller.device is expected
    assert controller.connected and expected.grabbed and controller.absinfo(1) is not None
    assert all(not device.grabbed and device.closed for device in devices if device is not expected)
    controller.disconnect()
    controller.disconnect()
    assert (expected.ungrabbed, expected.closed, controller.connected) == (True, True, False)
    assert (controller.device, controller.absinfo(1)) == (None, None)


def test_event_stream_lifecycle() -> None:
    async def first_event(controller: ctl.Controller):
        async with contextlib.aclosing(controller.events()) as stream:
            async for event in stream:
                return event
    async def cancel_reader(controller: ctl.Controller) -> list:
        seen: list = []
        async def consume() -> None:
            async for event in controller.events():
                seen.append(event)
        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return seen
    events = [FakeEvent(ctl.EV_ABS, 1, 100), FakeEvent(ctl.EV_ABS, 4, -100)]
    tail = [FakeEvent(ctl.EV_KEY, 305, 1), FakeEvent(ctl.EV_KEY, 305, 0)]
    all_events = [*events, FakeEvent(ctl.EV_KEY, 304, 1)]
    device = FakeInputDevice("/dev/input/event0", F310, events=all_events, batches=[tail])
    controller = ctl.Controller(CONFIG.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True
    assert collect_events(controller) == [*all_events, *tail]
    assert (device.stream_calls, device.stream_closed) == (1, True)
    device = FakeInputDevice("/dev/input/event0", F310, events=events, hold_open=True)
    controller = ctl.Controller(CONFIG.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True
    assert asyncio.run(first_event(controller)) == events[0]
    assert device.stream_closed is True
    device.stream_closed = False
    assert asyncio.run(cancel_reader(controller)) == events
    assert device.stream_closed is True
    device = FakeInputDevice("/dev/input/event0", F310, read_error=OSError("device gone"))
    controller = ctl.Controller(CONFIG.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True
    with pytest.raises(ctl.ControllerDisconnected):
        collect_events(controller)
    assert device.stream_closed is True
    idle = ctl.Controller(CONFIG.controller, evdev_mod=FakeEvdev([]))
    assert not idle.connected and collect_events(idle) == []

"""Tests for input normalization, the input mapper, and evdev discovery."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from config import load_config
from controller.controller import (
    EV_ABS,
    EV_KEY,
    Controller,
    ControllerDisconnected,
    InputMapper,
    apply_deadzone,
    normalize_axis,
)
from tests.fakes import FakeAbsInfo, FakeEvdev, FakeEvent, FakeInputDevice

DEFAULT = Path(__file__).parents[1] / "configs" / "default.toml"


@pytest.fixture
def config():
    return load_config(DEFAULT)


@pytest.fixture
def mapper(config) -> InputMapper:
    return InputMapper(config)


def collect_events(controller: Controller) -> list:
    async def collect():
        return [event async for event in controller.events()]

    return asyncio.run(collect())


def test_normalize_axis_symmetric_and_unipolar() -> None:
    assert normalize_axis(0, -32768, 32767) == 0.0
    assert normalize_axis(32767, -32768, 32767) == 1.0
    assert normalize_axis(-32768, -32768, 32767) == -1.0
    assert normalize_axis(16384, -32768, 32767) == pytest.approx(0.5, abs=1e-4)
    assert normalize_axis(-16384, -32768, 32767) == pytest.approx(-0.5, abs=1e-4)

    assert normalize_axis(0, 0, 255) == 0.0
    assert normalize_axis(255, 0, 255) == 1.0
    assert normalize_axis(128, 0, 255) == pytest.approx(128 / 255)

    assert normalize_axis(50000, -32768, 32767) == 1.0
    assert normalize_axis(-50000, -32768, 32767) == -1.0
    assert normalize_axis(-50, 0, 255) == 0.0
    assert normalize_axis(99999, 0, 255) == 1.0
    assert normalize_axis(10, 5, 5) == 0.0


def test_apply_deadzone() -> None:
    assert apply_deadzone(0.0, 0.1) == 0.0
    assert apply_deadzone(0.05, 0.1) == 0.0
    assert apply_deadzone(-0.09, 0.1) == 0.0
    assert apply_deadzone(0.55, 0.1) == pytest.approx(0.5)
    assert apply_deadzone(-0.55, 0.1) == pytest.approx(-0.5)
    assert apply_deadzone(1.0, 0.1) == 1.0
    assert apply_deadzone(0.5, 0.0) == 0.5
    assert apply_deadzone(-2.0, 0.0) == -1.0


def test_mapper_boots_disarmed_and_outputs_zero(mapper) -> None:
    assert mapper.armed is False

    mapper.process_event(EV_ABS, 1, 32767)

    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)
    assert mapper.motor_setpoints() == {"left": 0.0, "right": 0.0, "vertical": 0.0}


def test_cannot_arm_unless_sticks_and_triggers_are_neutral(mapper) -> None:
    mapper.process_event(EV_ABS, 1, 32767)
    mapper.process_event(EV_KEY, 304, 1)
    assert mapper.armed is False

    mapper.reset()
    mapper.process_event(EV_ABS, 2, 255)
    mapper.process_event(EV_KEY, 304, 1)
    assert mapper.armed is False

    mapper.process_event(EV_ABS, 2, 0)
    mapper.process_event(EV_KEY, 304, 1)
    assert mapper.armed is True


def test_disarm_is_immediate_and_never_rearms(mapper) -> None:
    mapper.process_event(EV_KEY, 304, 1)
    assert mapper.armed is True
    mapper.process_event(EV_ABS, 1, 32767)
    assert mapper.motor_setpoints()["left"] == pytest.approx(1.0, abs=1e-4)

    mapper.process_event(EV_KEY, 305, 1)
    assert mapper.armed is False
    assert all(value == 0.0 for value in mapper.motor_setpoints().values())

    for _ in range(3):
        mapper.process_event(EV_KEY, 305, 1)
        assert mapper.armed is False
        assert all(value == 0.0 for value in mapper.motor_setpoints().values())


def test_triggers_are_muxed_in_the_input_layer(mapper) -> None:
    mapper.process_event(EV_ABS, 2, 255)
    assert mapper.controls["vertical"] == 1.0

    mapper.process_event(EV_ABS, 5, 255)
    assert mapper.controls["vertical"] == 0.0

    mapper.process_event(EV_ABS, 5, 0)
    assert mapper.controls["vertical"] == 1.0

    mapper.process_event(EV_ABS, 2, 200)
    mapper.process_event(EV_ABS, 5, 100)
    assert mapper.controls["vertical"] == pytest.approx(100 / 255)

    assert "L_trig" not in mapper.controls
    assert "R_trig" not in mapper.controls


def test_reset_clears_triggers_and_sticks(mapper) -> None:
    mapper.process_event(EV_ABS, 2, 255)
    mapper.process_event(EV_ABS, 1, 32767)
    assert mapper.controls["vertical"] == 1.0
    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)

    mapper.reset()

    assert all(value == 0.0 for value in mapper.controls.values())

    # No stale trigger value may leak through after the reset.
    mapper.process_event(EV_ABS, 5, 255)
    assert mapper.controls["vertical"] == -1.0


def test_flip_buttons_toggle_per_motor_and_back_resets(mapper) -> None:
    mapper.process_event(EV_KEY, 304, 1)
    mapper.process_event(EV_ABS, 1, 32767)
    mapper.process_event(EV_ABS, 4, 32767)
    mapper.process_event(EV_ABS, 2, 255)

    setpoints = mapper.motor_setpoints()
    assert setpoints["left"] == pytest.approx(1.0, abs=1e-4)
    assert setpoints["right"] == pytest.approx(1.0, abs=1e-4)
    assert setpoints["vertical"] == pytest.approx(1.0)

    mapper.process_event(EV_KEY, 310, 1)  # L_bmp
    setpoints = mapper.motor_setpoints()
    assert setpoints["left"] == pytest.approx(-1.0, abs=1e-4)
    assert setpoints["right"] == pytest.approx(1.0, abs=1e-4)
    assert setpoints["vertical"] == pytest.approx(1.0)

    mapper.process_event(EV_KEY, 310, 1)  # Toggle back.
    assert mapper.motor_setpoints()["left"] == pytest.approx(1.0, abs=1e-4)

    mapper.process_event(EV_KEY, 310, 1)  # Toggle left again for the reset test.
    mapper.process_event(EV_KEY, 311, 1)  # R_bmp
    mapper.process_event(EV_KEY, 308, 1)  # Y
    setpoints = mapper.motor_setpoints()
    assert setpoints["left"] == pytest.approx(-1.0, abs=1e-4)
    assert setpoints["right"] == pytest.approx(-1.0, abs=1e-4)
    assert setpoints["vertical"] == pytest.approx(-1.0)

    mapper.process_event(EV_KEY, 314, 1)  # Back
    setpoints = mapper.motor_setpoints()
    assert setpoints["left"] == pytest.approx(1.0, abs=1e-4)
    assert setpoints["right"] == pytest.approx(1.0, abs=1e-4)
    assert setpoints["vertical"] == pytest.approx(1.0)


def test_key_code_one_does_not_collide_with_axis_one(mapper) -> None:
    assert mapper.process_event(EV_KEY, 1, 1) is False
    assert mapper.controls["L_Y"] == 0.0

    assert mapper.process_event(EV_ABS, 1, 32767) is True
    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)

    assert mapper.process_event(EV_KEY, 1, 0) is False
    assert mapper.controls["L_Y"] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("name", ["Logitech Gamepad F310", "Logitech Gamepad F710"])
def test_connect_matches_supported_devices(config, name: str) -> None:
    keyboard = FakeInputDevice("/dev/input/event0", "Keyboard")
    supported = FakeInputDevice("/dev/input/event1", name)
    ev = FakeEvdev([keyboard, supported])
    controller = Controller(config.controller, evdev_mod=ev)

    assert controller.connect() is True
    assert controller.connected is True
    assert controller.device is supported
    assert supported.grabbed is True
    assert keyboard.closed is True


def test_connect_continues_past_grab_error(config) -> None:
    busy = FakeInputDevice(
        "/dev/input/event1",
        "Logitech Gamepad F710",
        grab_error=OSError("device is busy"),
    )
    supported = FakeInputDevice("/dev/input/event2", "Logitech Gamepad F310")
    ev = FakeEvdev([busy, supported])
    controller = Controller(config.controller, evdev_mod=ev)

    assert controller.connect() is True
    assert controller.device is supported
    assert supported.grabbed is True
    assert busy.grabbed is False
    assert busy.closed is True


def test_events_yields_all_events_and_closes_stream(config) -> None:
    events = [
        FakeEvent(EV_ABS, 1, 100),
        FakeEvent(EV_ABS, 4, -100),
        FakeEvent(EV_KEY, 304, 1),
    ]
    batches = [[FakeEvent(EV_KEY, 305, 1), FakeEvent(EV_KEY, 305, 0)]]
    device = FakeInputDevice(
        "/dev/input/event0",
        "Logitech Gamepad F310",
        events=events,
        batches=batches,
    )
    controller = Controller(config.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True

    assert collect_events(controller) == events + batches[0]
    assert device.stream_calls == 1
    assert device.stream_closed is True


def test_events_closes_stream_when_consumer_breaks_early(config) -> None:
    events = [FakeEvent(EV_ABS, 1, 100), FakeEvent(EV_ABS, 4, -100)]
    device = FakeInputDevice(
        "/dev/input/event0",
        "Logitech Gamepad F310",
        events=events,
        hold_open=True,
    )
    controller = Controller(config.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True

    async def first_event():
        async with contextlib.aclosing(controller.events()) as stream:
            async for event in stream:
                return event
        return None

    assert asyncio.run(first_event()) == events[0]
    assert device.stream_closed is True


def test_events_closes_stream_on_cancellation(config) -> None:
    events = [FakeEvent(EV_ABS, 1, 100)]
    device = FakeInputDevice(
        "/dev/input/event0",
        "Logitech Gamepad F310",
        events=events,
        hold_open=True,
    )
    controller = Controller(config.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True

    async def cancel_reader():
        seen: list = []

        async def consume() -> None:
            async for event in controller.events():
                seen.append(event)

        task = asyncio.create_task(consume())
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return seen

    assert asyncio.run(cancel_reader()) == events
    assert device.stream_closed is True


def test_stream_error_raises_controller_disconnected(config) -> None:
    device = FakeInputDevice(
        "/dev/input/event0",
        "Logitech Gamepad F310",
        read_error=OSError("device disappeared"),
    )
    controller = Controller(config.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True

    with pytest.raises(ControllerDisconnected):
        collect_events(controller)

    assert device.stream_closed is True


def test_events_yields_nothing_when_not_connected(config) -> None:
    controller = Controller(config.controller, evdev_mod=FakeEvdev([]))

    assert controller.connected is False
    assert collect_events(controller) == []


def test_disconnect_ungrabs_closes_and_clears_state(config) -> None:
    device = FakeInputDevice(
        "/dev/input/event0",
        "Logitech Gamepad F310",
        absinfo={1: FakeAbsInfo(-32768, 32767)},
    )
    controller = Controller(config.controller, evdev_mod=FakeEvdev([device]))
    assert controller.connect() is True
    assert controller.absinfo(1) is not None

    controller.disconnect()

    assert device.ungrabbed is True
    assert device.closed is True
    assert controller.connected is False
    assert controller.device is None
    assert controller.absinfo(1) is None

    controller.disconnect()
    assert controller.connected is False

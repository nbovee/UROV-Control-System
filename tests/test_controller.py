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
VERTICALS = ("vertical_primary", "vertical_secondary")


def _code(labels: dict[int, str], label: str) -> int:
    return next(code for code, name in labels.items() if name == label)


def key(label: str, value: int = 1) -> tuple[int, int, int]:
    return (ctl.EV_KEY, _code(CONFIG.controller.buttons, label), value)


def axis(label: str, value: int) -> tuple[int, int, int]:
    return (ctl.EV_ABS, _code(CONFIG.controller.axes, label), value)


DPAD_X = _code(CONFIG.controller.axes, "Dpad_X")
DPAD_Y = _code(CONFIG.controller.axes, "Dpad_Y")

ARM, ESTOP = key("A"), key("B")
FLIP_Y, FLIP_LEFT, FLIP_RIGHT, BACK = key("Y"), key("L_bmp"), key("R_bmp"), key("Back")
L_Y_FULL, R_Y_FULL = axis("L_Y", 32767), axis("R_Y", 32767)
L_TRIG_FULL, L_TRIG_ZERO = axis("L_trig", 255), axis("L_trig", 0)
R_TRIG_FULL, R_TRIG_ZERO = axis("R_trig", 255), axis("R_trig", 0)

ZERO_SETPOINTS = {motor.name: 0.0 for motor in CONFIG.motors}
ZERO_TRIMS = {balance.name: 0.0 for balance in CONFIG.controller.balance}


@pytest.fixture
def mapper() -> ctl.InputMapper:
    return ctl.InputMapper(CONFIG)


def collect_events(controller: ctl.Controller) -> list:
    async def collect():
        return [event async for event in controller.events()]

    return asyncio.run(collect())


async def first_event(controller: ctl.Controller):
    async with contextlib.aclosing(controller.events()) as stream:
        async for event in stream:
            return event
    return None


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


def read_events(controller: ctl.Controller, mode: str):
    if mode == "collect":
        return collect_events(controller)
    if mode == "first":
        return asyncio.run(first_event(controller))
    return asyncio.run(cancel_reader(controller))


def assert_mapper_state(mapper: ctl.InputMapper, expected: dict) -> None:
    assert mapper.armed is expected["armed"]
    for label, value in expected.get("controls", {}).items():
        assert mapper.controls[label] == pytest.approx(value)
    for label in expected.get("absent_controls", ()):
        assert label not in mapper.controls
    for name, value in expected.get("setpoints", {}).items():
        assert mapper.motor_setpoints()[name] == pytest.approx(value)
    for name, value in expected.get("trims", {}).items():
        assert mapper.trims[name] == pytest.approx(value)


@pytest.mark.parametrize(
    ("value", "minimum", "maximum", "expected"),
    [
        pytest.param(0, -32768, 32767, 0.0, id="center-signed"),
        pytest.param(32767, -32768, 32767, 1.0, id="max-signed"),
        pytest.param(-32768, -32768, 32767, -1.0, id="min-signed"),
        pytest.param(16384, -32768, 32767, 0.5, id="half-signed"),
        pytest.param(-16384, -32768, 32767, -0.5, id="negative-half-signed"),
        pytest.param(0, 0, 255, 0.0, id="min-unsigned"),
        pytest.param(255, 0, 255, 1.0, id="max-unsigned"),
        pytest.param(128, 0, 255, 128 / 255, id="mid-unsigned"),
        pytest.param(50000, -32768, 32767, 1.0, id="clamp-high"),
        pytest.param(-50000, -32768, 32767, -1.0, id="clamp-low"),
        pytest.param(-50, 0, 255, 0.0, id="clamp-low-unsigned"),
        pytest.param(99999, 0, 255, 1.0, id="clamp-high-unsigned"),
        pytest.param(10, 5, 5, 0.0, id="degenerate-range"),
    ],
)
def test_normalize_axis(value, minimum, maximum, expected) -> None:
    assert ctl.normalize_axis(value, minimum, maximum) == pytest.approx(expected, abs=1e-4)


@pytest.mark.parametrize(
    ("value", "deadzone", "expected"),
    [
        pytest.param(0.0, 0.1, 0.0, id="zero"),
        pytest.param(0.05, 0.1, 0.0, id="inside-deadzone"),
        pytest.param(-0.09, 0.1, 0.0, id="negative-inside-deadzone"),
        pytest.param(0.55, 0.1, 0.5, id="rescaled-positive"),
        pytest.param(-0.55, 0.1, -0.5, id="rescaled-negative"),
        pytest.param(1.0, 0.1, 1.0, id="full-positive"),
        pytest.param(0.5, 0.0, 0.5, id="no-deadzone-passthrough"),
        pytest.param(-2.0, 0.0, -1.0, id="no-deadzone-clamps"),
    ],
)
def test_apply_deadzone(value, deadzone, expected) -> None:
    assert ctl.apply_deadzone(value, deadzone) == pytest.approx(expected)


MAPPER_CASES = [
    pytest.param(
        [],
        {"armed": False, "setpoints": ZERO_SETPOINTS, "trims": ZERO_TRIMS},
        id="initial-disarmed-neutral",
    ),
    pytest.param(
        [L_Y_FULL, R_Y_FULL, L_TRIG_FULL],
        {
            "armed": False,
            "controls": {"L_Y": 1.0, "R_Y": 1.0, "vertical": 1.0},
            "setpoints": ZERO_SETPOINTS,
        },
        id="disarmed-ignores-sticks",
    ),
    pytest.param(
        [L_Y_FULL, ARM],
        {"armed": False, "controls": {"L_Y": 1.0}, "setpoints": ZERO_SETPOINTS},
        id="arm-rejected-when-stick-off-neutral",
    ),
    pytest.param(
        [L_TRIG_FULL, ARM],
        {"armed": False, "controls": {"vertical": 1.0}, "setpoints": ZERO_SETPOINTS},
        id="arm-rejected-when-trigger-off-neutral",
    ),
    pytest.param(
        [L_TRIG_FULL, ARM, L_TRIG_ZERO, ARM],
        {"armed": True, "controls": {"vertical": 0.0}},
        id="neutral-arm-succeeds",
    ),
    pytest.param(
        [ARM, L_Y_FULL],
        {
            "armed": True,
            "controls": {"L_Y": 1.0, "R_Y": 0.0, "vertical": 0.0},
            "setpoints": {"left": 1.0},
        },
        id="armed-drives-left",
    ),
    pytest.param(
        [ARM, L_Y_FULL, ESTOP, ESTOP, ESTOP, ESTOP],
        {"armed": False, "controls": {"L_Y": 1.0}, "setpoints": ZERO_SETPOINTS},
        id="estop-disarms-and-stays-disarmed",
    ),
    pytest.param(
        [ARM, L_TRIG_FULL],
        {
            "armed": True,
            "controls": {"vertical": 1.0},
            "absent_controls": ("L_trig", "R_trig"),
            "setpoints": dict.fromkeys(VERTICALS, 1.0),
        },
        id="left-trigger-drives-both-verticals",
    ),
    pytest.param(
        [ARM, R_TRIG_FULL],
        {
            "armed": True,
            "controls": {"vertical": -1.0},
            "setpoints": dict.fromkeys(VERTICALS, -1.0),
        },
        id="right-trigger-reverses-both-verticals",
    ),
    pytest.param(
        [ARM, L_TRIG_FULL, R_TRIG_FULL],
        {
            "armed": True,
            "controls": {"vertical": 0.0},
            "setpoints": dict.fromkeys(VERTICALS, 0.0),
        },
        id="opposing-triggers-cancel",
    ),
    pytest.param(
        [ARM, L_TRIG_FULL, R_TRIG_FULL, R_TRIG_ZERO],
        {
            "armed": True,
            "controls": {"vertical": 1.0},
            "setpoints": dict.fromkeys(VERTICALS, 1.0),
        },
        id="releasing-right-trigger-restores-left",
    ),
    pytest.param(
        [ARM, axis("L_trig", 200), axis("R_trig", 100)],
        {
            "armed": True,
            "controls": {"vertical": 100 / 255},
            "setpoints": dict.fromkeys(VERTICALS, 100 / 255),
        },
        id="mixed-triggers-use-difference",
    ),
    pytest.param(
        [ARM, L_Y_FULL, R_Y_FULL, L_TRIG_FULL],
        {
            "armed": True,
            "controls": {"L_Y": 1.0, "R_Y": 1.0, "vertical": 1.0},
            "setpoints": {
                "left": 1.0,
                "right": 1.0,
                **dict.fromkeys(VERTICALS, 1.0),
            },
        },
        id="all-controls-drive-forward",
    ),
    pytest.param(
        [ARM, L_Y_FULL, R_Y_FULL, L_TRIG_FULL, FLIP_LEFT],
        {
            "armed": True,
            "setpoints": {
                "left": -1.0,
                "right": 1.0,
                **dict.fromkeys(VERTICALS, 1.0),
            },
        },
        id="left-flip-inverts-only-left",
    ),
    pytest.param(
        [ARM, L_Y_FULL, R_Y_FULL, L_TRIG_FULL, FLIP_LEFT, FLIP_LEFT],
        {
            "armed": True,
            "setpoints": {
                "left": 1.0,
                "right": 1.0,
                **dict.fromkeys(VERTICALS, 1.0),
            },
        },
        id="left-flip-toggles-back",
    ),
    pytest.param(
        [ARM, L_Y_FULL, R_Y_FULL, L_TRIG_FULL, FLIP_LEFT, FLIP_RIGHT, FLIP_Y],
        {
            "armed": True,
            "setpoints": {
                "left": -1.0,
                "right": -1.0,
                **dict.fromkeys(VERTICALS, -1.0),
            },
        },
        id="all-flips-invert-every-motor",
    ),
    pytest.param(
        [
            ARM,
            L_Y_FULL,
            R_Y_FULL,
            L_TRIG_FULL,
            axis("Dpad_X", -1),
            axis("Dpad_Y", 1),
            FLIP_Y,
        ],
        {
            "armed": True,
            "trims": {"horizontal": -0.05, "vertical": 0.05},
            "setpoints": {
                "left": 0.95,
                "right": 1.0,
                "vertical_primary": -1.0,
                "vertical_secondary": -0.95,
            },
        },
        id="trims-scale-primary-and-secondary",
    ),
    pytest.param(
        [
            ARM,
            L_Y_FULL,
            R_Y_FULL,
            L_TRIG_FULL,
            axis("Dpad_X", -1),
            axis("Dpad_Y", 1),
            FLIP_Y,
            BACK,
        ],
        {
            "armed": True,
            "trims": ZERO_TRIMS,
            "setpoints": {
                "left": 1.0,
                "right": 1.0,
                **dict.fromkeys(VERTICALS, 1.0),
            },
        },
        id="back-resets-flips-and-trims",
    ),
]


@pytest.mark.parametrize(("events", "expected"), MAPPER_CASES)
def test_input_mapper_event_sequences(mapper, events, expected) -> None:
    for event in events:
        mapper.process_event(*event)
    assert_mapper_state(mapper, expected)


@pytest.mark.parametrize(
    ("events", "matches", "controls"),
    [
        pytest.param(
            ((ctl.EV_KEY, 1, 1), (ctl.EV_ABS, 1, 32767), (ctl.EV_KEY, 1, 0)),
            (False, True, False),
            {"L_Y": 1.0},
            id="key-code-1-does-not-touch-abs-axis-1",
        ),
        pytest.param(
            ((ctl.EV_ABS, 1, 32767), (ctl.EV_KEY, 1, 0), (ctl.EV_KEY, 1, 1)),
            (True, False, False),
            {"L_Y": 1.0},
            id="abs-axis-1-does-not-touch-key-code-1",
        ),
    ],
)
def test_event_binding_isolation(mapper, events, matches, controls) -> None:
    assert [mapper.process_event(*event) for event in events] == list(matches)
    for label, value in controls.items():
        assert mapper.controls[label] == pytest.approx(value)


@pytest.mark.parametrize(
    ("code", "value", "trimmed", "other"),
    [
        pytest.param(DPAD_X, -1, "left", "right", id="dpad-left-trims-left-down"),
        pytest.param(DPAD_X, 1, "right", "left", id="dpad-right-trims-right-down"),
        pytest.param(
            DPAD_Y, -1, "vertical_primary", "vertical_secondary", id="dpad-up-trims-primary"
        ),
        pytest.param(
            DPAD_Y, 1, "vertical_secondary", "vertical_primary", id="dpad-down-trims-secondary"
        ),
    ],
)
def test_dpad_balance_trim(mapper, code, value, trimmed, other) -> None:
    for event in (ARM, L_Y_FULL, R_Y_FULL, L_TRIG_FULL):
        mapper.process_event(*event)
    mapper.process_event(ctl.EV_ABS, code, value)
    setpoints = mapper.motor_setpoints()
    assert setpoints[trimmed] == pytest.approx(0.95)
    assert setpoints[other] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("code", "trim", "values", "expected"),
    [
        pytest.param(
            DPAD_X,
            "horizontal",
            (-1, -1, 0, -1),
            (-0.05, -0.05, -0.05, -0.10),
            id="dpad-x-left-edge-detected",
        ),
        pytest.param(
            DPAD_X,
            "horizontal",
            (1, 1, 0, 1),
            (0.05, 0.05, 0.05, 0.10),
            id="dpad-x-right-edge-detected",
        ),
        pytest.param(
            DPAD_Y,
            "vertical",
            (-1, -1, 0, -1),
            (-0.05, -0.05, -0.05, -0.10),
            id="dpad-y-up-edge-detected",
        ),
        pytest.param(
            DPAD_Y,
            "vertical",
            (1, 1, 0, 1),
            (0.05, 0.05, 0.05, 0.10),
            id="dpad-y-down-edge-detected",
        ),
    ],
)
def test_dpad_steps_only_on_new_press(mapper, code, trim, values, expected) -> None:
    for value, want in zip(values, expected, strict=True):
        mapper.process_event(ctl.EV_ABS, code, value)
        assert mapper.trims[trim] == pytest.approx(want)


@pytest.mark.parametrize(
    ("code", "trim", "values", "expected"),
    [
        pytest.param(
            DPAD_X, "horizontal", (-1, 0) * 10, -0.30, id="dpad-x-left-clamps-at-minus-max"
        ),
        pytest.param(DPAD_X, "horizontal", (1, 0) * 12, 0.30, id="dpad-x-right-clamps-at-plus-max"),
        pytest.param(DPAD_Y, "vertical", (-1, 0) * 10, -0.30, id="dpad-y-up-clamps-at-minus-max"),
        pytest.param(DPAD_Y, "vertical", (1, 0) * 12, 0.30, id="dpad-y-down-clamps-at-plus-max"),
    ],
)
def test_dpad_trim_clamps_at_max(mapper, code, trim, values, expected) -> None:
    for value in values:
        mapper.process_event(ctl.EV_ABS, code, value)
    assert mapper.trims[trim] == pytest.approx(expected)


@pytest.mark.parametrize(
    "dpad_events",
    [
        pytest.param((axis("Dpad_X", 1),), id="dpad-x"),
        pytest.param((axis("Dpad_Y", -1),), id="dpad-y"),
        pytest.param((axis("Dpad_X", 1), axis("Dpad_Y", -1)), id="both-axes"),
    ],
)
def test_dpad_does_not_block_neutral_arming(mapper, dpad_events) -> None:
    for event in dpad_events:
        mapper.process_event(*event)
    assert mapper.is_neutral() is True
    mapper.process_event(*ARM)
    assert mapper.armed is True
    assert mapper.motor_setpoints() == pytest.approx(ZERO_SETPOINTS)


@pytest.mark.parametrize(
    ("disarm", "armed", "trims"),
    [
        pytest.param(
            False,
            True,
            {"horizontal": -0.05, "vertical": 0.0},
            id="watchdog-reset-preserves-trim-while-armed",
        ),
        pytest.param(
            True,
            False,
            ZERO_TRIMS,
            id="disconnect-reset-clears-trim",
        ),
    ],
)
def test_reset_policy(mapper, disarm, armed, trims) -> None:
    for event in (ARM, axis("Dpad_X", -1), L_Y_FULL, L_TRIG_FULL):
        mapper.process_event(*event)
    mapper.reset(disarm=disarm)
    assert mapper.armed is armed
    assert mapper.trims == pytest.approx(trims)
    assert all(value == 0.0 for value in mapper.controls.values())
    assert mapper.motor_setpoints() == pytest.approx(ZERO_SETPOINTS)
    mapper.process_event(*R_TRIG_FULL)
    assert mapper.controls["vertical"] == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("names", "grab_error", "expected_index"),
    [
        pytest.param((F310,), None, 1, id="f310"),
        pytest.param((F710,), None, 1, id="f710"),
        pytest.param((F710, F310), OSError("busy"), 2, id="skip-busy-device"),
    ],
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


STREAM_EVENTS = [FakeEvent(ctl.EV_ABS, 1, 100), FakeEvent(ctl.EV_ABS, 4, -100)]
STREAM_TAIL = [FakeEvent(ctl.EV_KEY, 305, 1), FakeEvent(ctl.EV_KEY, 305, 0)]
STREAM_ALL = [*STREAM_EVENTS, FakeEvent(ctl.EV_KEY, 304, 1), *STREAM_TAIL]

STREAM_CASES = [
    pytest.param(
        {"events": STREAM_ALL[:-2], "batches": [STREAM_TAIL]},
        "collect",
        True,
        STREAM_ALL,
        None,
        1,
        id="exhausts-events-and-batches",
    ),
    pytest.param(
        {"events": STREAM_EVENTS, "hold_open": True},
        "first",
        True,
        STREAM_EVENTS[0],
        None,
        1,
        id="early-break-closes-stream",
    ),
    pytest.param(
        {"events": STREAM_EVENTS, "hold_open": True},
        "cancel",
        True,
        STREAM_EVENTS,
        None,
        1,
        id="cancellation-closes-stream",
    ),
    pytest.param(
        {"read_error": OSError("device gone")},
        "collect",
        True,
        None,
        ctl.ControllerDisconnected,
        1,
        id="read-error-becomes-disconnected",
    ),
    pytest.param(
        None,
        "collect",
        False,
        [],
        None,
        0,
        id="disconnected-yields-nothing",
    ),
]


@pytest.mark.parametrize(
    ("device_kwargs", "mode", "connect_expected", "expected", "error", "stream_calls"),
    STREAM_CASES,
)
def test_event_stream_scenarios(
    device_kwargs, mode, connect_expected, expected, error, stream_calls
) -> None:
    if device_kwargs is None:
        device = None
        controller = ctl.Controller(CONFIG.controller, evdev_mod=FakeEvdev([]))
    else:
        device = FakeInputDevice("/dev/input/event0", F310, **device_kwargs)
        controller = ctl.Controller(CONFIG.controller, evdev_mod=FakeEvdev([device]))

    assert controller.connect() is connect_expected
    if error is not None:
        with pytest.raises(error):
            read_events(controller, mode)
    else:
        assert read_events(controller, mode) == expected

    if device is not None:
        assert (device.stream_calls, device.stream_closed) == (stream_calls, True)

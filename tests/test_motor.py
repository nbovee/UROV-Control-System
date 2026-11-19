"""Tests for the PCA9685 driver and IBT-2 motor abstraction."""

from __future__ import annotations

import pytest

from motor.motor import IBT2Motor
from motor.pca9685 import (
    ALL_LED_OFF_H,
    LED0_ON_L,
    MODE1,
    MODE1_AI,
    MODE1_RESTART,
    MODE2,
    MODE2_OUTDRV,
    PCA9685,
    PRESCALE,
)
from tests.fakes import FakePCA9685, FakeSMBus

ADDRESS = 0x40


def make_pca(frequency: int = 1000) -> tuple[PCA9685, FakeSMBus]:
    bus = FakeSMBus()
    pca = PCA9685(
        bus_num=1,
        address=ADDRESS,
        frequency=frequency,
        bus=bus,
        sleep=lambda _: None,
    )
    return pca, bus


def make_motor(
    *, invert: bool = False, max_duty: float = 0.5
) -> tuple[IBT2Motor, FakePCA9685]:
    pca = FakePCA9685()
    motor = IBT2Motor(pca, rpwm=2, lpwm=4, max_duty=max_duty, invert=invert)
    return motor, pca


def assert_never_drives_both_channels(
    pca: FakePCA9685, rpwm: int, lpwm: int, start: dict[int, float]
) -> None:
    duties = dict(start)
    for call in pca.calls:
        if call[0] != "set_duty":
            continue
        _, channel, duty = call
        duties[channel] = duty
        assert not (
            duties[rpwm] > 0.0 and duties[lpwm] > 0.0
        ), f"both IBT-2 inputs were active after {call!r}"


def test_initialization_writes_registers_and_prescale() -> None:
    _, bus = make_pca()

    assert bus.writes[0] == (ADDRESS, MODE2, MODE2_OUTDRV)
    assert bus.writes[1] == (ADDRESS, MODE1, MODE1_AI)
    assert (ADDRESS, ALL_LED_OFF_H, 0x10) in bus.writes
    # 25 MHz / (4096 * 1000 Hz) - 1 == 5
    assert (ADDRESS, PRESCALE, 5) in bus.writes
    assert bus.registers[MODE1] == MODE1_AI | MODE1_RESTART


@pytest.mark.parametrize(
    ("duty", "payload"),
    [
        pytest.param(0.0, [0, 0, 0, 0x10], id="off"),
        pytest.param(0.5, [0, 0, 0, 0x08], id="half"),
        pytest.param(1.0, [0, 0x10, 0, 0], id="full-on"),
        pytest.param(-0.5, [0, 0, 0, 0x10], id="clamp-low"),
        pytest.param(1.5, [0, 0x10, 0, 0], id="clamp-high"),
    ],
)
def test_set_duty_encodes_and_clamps(duty: float, payload: list[int]) -> None:
    pca, bus = make_pca()

    pca.set_duty(3, duty)

    assert bus.block_writes[-1] == (ADDRESS, LED0_ON_L + 12, payload)


def test_all_off_writes_global_off_register() -> None:
    pca, bus = make_pca()

    pca.all_off()

    assert bus.writes[-1] == (ADDRESS, ALL_LED_OFF_H, 0x10)


def test_close_is_idempotent_and_closes_owned_bus_on_error() -> None:
    pca, bus = make_pca()
    pca._owns_bus = True  # Pretend the driver opened the bus itself.
    bus.fail_all_writes = True

    pca.close()
    pca.close()

    assert bus.closed is True
    assert bus.close_calls == 1


@pytest.mark.parametrize(
    "channel",
    [pytest.param(16, id="too-high"), pytest.param(-1, id="negative")],
)
def test_invalid_channel_raises(channel: int) -> None:
    pca, _ = make_pca()

    with pytest.raises(ValueError, match="channel"):
        pca.set_duty(channel, 0.5)


@pytest.mark.parametrize(
    "frequency",
    [pytest.param(23, id="too-low"), pytest.param(1527, id="too-high")],
)
def test_invalid_frequency_raises(frequency: int) -> None:
    with pytest.raises(ValueError, match="frequency"):
        PCA9685(address=ADDRESS, frequency=frequency, bus=FakeSMBus())


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        pytest.param({"rpwm": -1, "lpwm": 4}, "rpwm", id="rpwm-negative"),
        pytest.param({"rpwm": 16, "lpwm": 4}, "rpwm", id="rpwm-too-high"),
        pytest.param({"rpwm": 2, "lpwm": -1}, "lpwm", id="lpwm-negative"),
        pytest.param({"rpwm": 2, "lpwm": 16}, "lpwm", id="lpwm-too-high"),
        pytest.param({"rpwm": 2, "lpwm": 2}, "different channels", id="same-channel"),
        pytest.param(
            {"rpwm": 2, "lpwm": 4, "max_duty": 0.0},
            "max_duty",
            id="max-duty-zero",
        ),
        pytest.param(
            {"rpwm": 2, "lpwm": 4, "max_duty": -0.1},
            "max_duty",
            id="max-duty-negative",
        ),
        pytest.param(
            {"rpwm": 2, "lpwm": 4, "max_duty": 1.5},
            "max_duty",
            id="max-duty-too-high",
        ),
    ],
)
def test_invalid_ibt2_init_raises(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        IBT2Motor(FakePCA9685(), **kwargs)


@pytest.mark.parametrize(
    ("throttle", "invert", "max_duty", "expected_calls"),
    [
        pytest.param(
            1.0,
            False,
            0.5,
            [("set_duty", 4, 0.0), ("set_duty", 2, 0.5)],
            id="forward-zeroes-lpwm-first",
        ),
        pytest.param(
            -1.0,
            False,
            0.5,
            [("set_duty", 2, 0.0), ("set_duty", 4, 0.5)],
            id="reverse-zeroes-rpwm-first",
        ),
        pytest.param(
            0.0,
            False,
            0.5,
            [("set_duty", 2, 0.0), ("set_duty", 4, 0.0)],
            id="zero-stops-both",
        ),
        pytest.param(
            5.0,
            False,
            0.5,
            [("set_duty", 4, 0.0), ("set_duty", 2, 0.5)],
            id="clamp-forward",
        ),
        pytest.param(
            -5.0,
            False,
            0.5,
            [("set_duty", 2, 0.0), ("set_duty", 4, 0.5)],
            id="clamp-reverse",
        ),
        pytest.param(
            1.0,
            True,
            0.5,
            [("set_duty", 2, 0.0), ("set_duty", 4, 0.5)],
            id="invert-forward",
        ),
        pytest.param(
            -1.0,
            True,
            0.5,
            [("set_duty", 4, 0.0), ("set_duty", 2, 0.5)],
            id="invert-reverse",
        ),
        pytest.param(
            0.5,
            False,
            0.4,
            [("set_duty", 4, 0.0), ("set_duty", 2, 0.2)],
            id="max-duty-forward",
        ),
        pytest.param(
            -0.5,
            False,
            0.4,
            [("set_duty", 2, 0.0), ("set_duty", 4, 0.2)],
            id="max-duty-reverse",
        ),
    ],
)
def test_output_drives_one_channel_and_zeroes_the_other(
    throttle: float, invert: bool, max_duty: float, expected_calls: list[tuple]
) -> None:
    motor, pca = make_motor(invert=invert, max_duty=max_duty)

    motor.output(throttle)

    assert pca.calls == expected_calls


def test_direction_reversal_never_drives_both_channels() -> None:
    motor, pca = make_motor()
    motor.output(1.0)
    start = dict(pca.duties)
    pca.calls.clear()

    for throttle in (-1.0, 1.0, -0.25, 0.75):
        motor.output(throttle)

    assert_never_drives_both_channels(pca, 2, 4, start)
    assert pca.duties[2] == pytest.approx(0.375)
    assert pca.duties[4] == 0.0


def test_zero_and_stop_zero_both_channels() -> None:
    motor, pca = make_motor()

    motor.output(-1.0)
    motor.output(0.0)
    assert (pca.duties[2], pca.duties[4]) == (0.0, 0.0)

    motor.output(1.0)
    motor.stop()
    assert (pca.duties[2], pca.duties[4]) == (0.0, 0.0)

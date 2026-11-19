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


def test_set_duty_full_off_full_on_and_half() -> None:
    pca, bus = make_pca()

    pca.set_duty(3, 0.0)
    assert bus.block_writes[-1] == (ADDRESS, LED0_ON_L + 12, [0, 0, 0, 0x10])

    pca.set_duty(3, 1.0)
    assert bus.block_writes[-1] == (ADDRESS, LED0_ON_L + 12, [0, 0x10, 0, 0])

    pca.set_duty(3, 0.5)
    address, register, data = bus.block_writes[-1]
    counts = data[2] | ((data[3] & 0x0F) << 8)
    assert address == ADDRESS
    assert register == LED0_ON_L + 12
    assert counts == 2048


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


def test_invalid_channel_and_frequency_raise() -> None:
    pca, _ = make_pca()

    with pytest.raises(ValueError, match="channel"):
        pca.set_duty(16, 0.5)
    with pytest.raises(ValueError, match="channel"):
        pca.set_duty(-1, 0.5)
    with pytest.raises(ValueError, match="frequency"):
        PCA9685(address=ADDRESS, frequency=23, bus=FakeSMBus())
    with pytest.raises(ValueError, match="frequency"):
        PCA9685(address=ADDRESS, frequency=1527, bus=FakeSMBus())


def test_positive_throttle_zeroes_lpwm_before_driving_rpwm() -> None:
    motor, pca = make_motor()

    motor.output(1.0)

    assert pca.calls == [("set_duty", 4, 0.0), ("set_duty", 2, 0.5)]
    assert pca.duties[4] == 0.0
    assert pca.duties[2] == 0.5


def test_negative_throttle_zeroes_rpwm_before_driving_lpwm() -> None:
    motor, pca = make_motor()

    motor.output(-1.0)

    assert pca.calls == [("set_duty", 2, 0.0), ("set_duty", 4, 0.5)]
    assert pca.duties[2] == 0.0
    assert pca.duties[4] == 0.5


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


def test_throttle_is_clamped_and_invert_flips_direction() -> None:
    motor, pca = make_motor()

    motor.output(5.0)
    assert pca.duties[2] == 0.5
    assert pca.duties[4] == 0.0

    motor.output(-5.0)
    assert pca.duties[2] == 0.0
    assert pca.duties[4] == 0.5

    inverted, inverted_pca = make_motor(invert=True)
    inverted.output(1.0)
    assert inverted_pca.duties[2] == 0.0
    assert inverted_pca.duties[4] == 0.5


def test_zero_and_stop_zero_both_channels() -> None:
    motor, pca = make_motor()

    motor.output(-1.0)
    motor.output(0.0)
    assert pca.duties[2] == 0.0
    assert pca.duties[4] == 0.0

    motor.output(1.0)
    motor.stop()
    assert pca.duties[2] == 0.0
    assert pca.duties[4] == 0.0

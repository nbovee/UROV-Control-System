"""Tests for MotorSystem actuation and fault-tolerant shutdown."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from config import Config, MotorConfig, load_config
from motor.system import MotorSystem
from tests.fakes import FakePCA9685

DEFAULT = Path(__file__).parents[1] / "configs" / "default.toml"


def default_config() -> Config:
    return load_config(DEFAULT)


def counter_rotated_config() -> Config:
    config = load_config(DEFAULT)
    motors = tuple(
        replace(motor, invert=True, max_duty=0.4)
        if motor.name == "vertical_secondary"
        else motor
        for motor in config.motors
    )
    return replace(config, motors=motors)


def five_motor_config() -> Config:
    config = load_config(DEFAULT)
    fifth = MotorConfig(name="fifth", rpwm=8, lpwm=9, control="L_X")
    return replace(config, motors=(*config.motors, fifth))


def make_system(
    config: Config | None = None, pca: FakePCA9685 | None = None
) -> tuple[MotorSystem, FakePCA9685]:
    config = config or load_config(DEFAULT)
    pca = pca or FakePCA9685()
    return MotorSystem(config, pca=pca), pca


APPLY_CASES = [
    pytest.param(
        default_config,
        (),
        {
            "left": 1.0,
            "right": -1.0,
            "vertical_primary": 0.5,
            "vertical_secondary": -0.5,
        },
        {0: 0.0, 1: 0.25, 2: 0.5, 3: 0.0, 4: 0.0, 5: 0.5, 6: 0.0, 7: 0.25},
        id="standard",
    ),
    pytest.param(
        default_config,
        (
            {
                "left": 1.0,
                "right": -1.0,
                "vertical_primary": 1.0,
                "vertical_secondary": 1.0,
            },
        ),
        {"left": 0.5},
        {0: 0.0, 1: 0.0, 2: 0.25, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0, 7: 0.0},
        id="omitted-defaults-to-zero",
    ),
    pytest.param(
        counter_rotated_config,
        (),
        {"vertical_primary": 0.5, "vertical_secondary": 0.5},
        {0: 0.0, 1: 0.25, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0, 7: 0.2},
        id="counter-rotated-verticals",
    ),
    pytest.param(
        five_motor_config,
        (),
        {
            "left": 1.0,
            "right": -1.0,
            "vertical_primary": 0.5,
            "vertical_secondary": 0.5,
            "fifth": -0.5,
        },
        {
            0: 0.0,
            1: 0.25,
            2: 0.5,
            3: 0.0,
            4: 0.0,
            5: 0.5,
            6: 0.25,
            7: 0.0,
            8: 0.0,
            9: 0.25,
        },
        id="five-motors",
    ),
]


@pytest.mark.parametrize(
    ("config_factory", "before", "setpoints", "expected"), APPLY_CASES
)
def test_apply_drives_expected_channels(
    config_factory: Callable[[], Config],
    before: tuple[dict[str, float], ...],
    setpoints: dict[str, float],
    expected: dict[int, float],
) -> None:
    config = config_factory()
    system, pca = make_system(config)

    for prior in before:
        system.apply(prior)

    system.apply(setpoints)

    assert system.motor_names == tuple(motor.name for motor in config.motors)
    assert {channel: pca.duties[channel] for channel in expected} == expected
    assert all(
        duty == 0.0 for channel, duty in pca.duties.items() if channel not in expected
    )


def test_startup_zeroes_every_motor() -> None:
    system, pca = make_system()

    assert pca.all_off_calls == 1
    assert all(duty == 0.0 for duty in pca.duties.values())
    assert system.motor_names == ("left", "right", "vertical_primary", "vertical_secondary")


FAULT_CASES = [
    pytest.param("stop_all", 1, False, 0, id="stop-all"),
    pytest.param("cleanup", 2, True, 1, id="cleanup-idempotent"),
]


@pytest.mark.parametrize(("action", "calls", "closed", "close_calls"), FAULT_CASES)
def test_fault_isolation_continues_stopping_and_cleans_up(
    action: str, calls: int, closed: bool, close_calls: int
) -> None:
    pca = FakePCA9685()
    system, _ = make_system(pca=pca)
    system.apply(
        {
            "left": 1.0,
            "right": -1.0,
            "vertical_primary": 1.0,
            "vertical_secondary": 1.0,
        }
    )

    pca.fail_all_off = True
    pca.fail_channels = {2}

    for _ in range(calls):
        getattr(system, action)()

    assert pca.duties[2] == 0.5
    assert all(duty == 0.0 for channel, duty in pca.duties.items() if channel != 2)
    assert pca.closed is closed
    assert pca.close_calls == close_calls

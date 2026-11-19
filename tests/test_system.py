"""Tests for MotorSystem actuation and fault-tolerant shutdown."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from config import MotorConfig, load_config
from motor.system import MotorSystem
from tests.fakes import FakePCA9685

DEFAULT = Path(__file__).parents[1] / "configs" / "default.toml"


def make_system(config=None, pca=None) -> tuple[MotorSystem, FakePCA9685]:
    config = config or load_config(DEFAULT)
    pca = pca or FakePCA9685()
    return MotorSystem(config, pca=pca), pca


def test_startup_zeroes_every_motor() -> None:
    system, pca = make_system()

    assert pca.all_off_calls == 1
    assert all(duty == 0.0 for duty in pca.duties.values())
    assert system.motor_names == ("left", "right", "vertical")


def test_apply_drives_all_configured_motors() -> None:
    system, pca = make_system()

    system.apply({"left": 1.0, "right": -1.0, "vertical": 0.5})

    assert pca.duties[2] == 0.5  # left R_PWM
    assert pca.duties[4] == 0.0  # left L_PWM
    assert pca.duties[0] == 0.0  # right R_PWM
    assert pca.duties[5] == 0.5  # right L_PWM
    assert pca.duties[1] == 0.25  # vertical R_PWM
    assert pca.duties[3] == 0.0  # vertical L_PWM


def test_apply_defaults_omitted_motors_to_zero() -> None:
    system, pca = make_system()
    system.apply({"left": 1.0, "right": -1.0, "vertical": 1.0})

    system.apply({"left": 0.5})

    assert pca.duties[2] == 0.25
    assert pca.duties[4] == 0.0
    assert pca.duties[0] == 0.0
    assert pca.duties[5] == 0.0
    assert pca.duties[1] == 0.0
    assert pca.duties[3] == 0.0


def test_four_motor_config_drives_fourth_motor() -> None:
    config = load_config(DEFAULT)
    fourth = MotorConfig(name="fourth", rpwm=6, lpwm=7, control="L_X")
    config = replace(config, motors=(*config.motors, fourth))
    system, pca = make_system(config)

    system.apply(
        {"left": 1.0, "right": -1.0, "vertical": 0.5, "fourth": -0.5}
    )

    assert system.motor_names[-1] == "fourth"
    assert pca.duties[6] == 0.0
    assert pca.duties[7] == 0.25


def test_stop_all_continues_after_pca_and_motor_errors() -> None:
    pca = FakePCA9685()
    system, _ = make_system(pca=pca)
    system.apply({"left": 1.0, "right": -1.0, "vertical": 1.0})

    pca.fail_all_off = True
    pca.fail_channels = {2}

    system.stop_all()

    # The injected failure left the left R_PWM channel untouched...
    assert pca.duties[2] == 0.5
    # ...but every other motor was still stopped.
    assert pca.duties[0] == 0.0
    assert pca.duties[5] == 0.0
    assert pca.duties[1] == 0.0
    assert pca.duties[3] == 0.0


def test_cleanup_is_idempotent_and_closes_pca_after_errors() -> None:
    pca = FakePCA9685()
    system, _ = make_system(pca=pca)
    system.apply({"left": 1.0, "right": -1.0, "vertical": 1.0})

    pca.fail_all_off = True
    pca.fail_channels = {2}

    system.cleanup()
    system.cleanup()

    assert pca.closed is True
    assert pca.close_calls == 1
    assert pca.duties[0] == 0.0
    assert pca.duties[5] == 0.0
    assert pca.duties[1] == 0.0
    assert pca.duties[3] == 0.0

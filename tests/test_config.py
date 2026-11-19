"""Configuration loading and validation tests."""

from pathlib import Path

import pytest

from config import load_config

DEFAULT = Path(__file__).parents[1] / "configs" / "default.toml"


def write_variant(tmp_path: Path, old: str, new: str, *, name: str = "variant.toml"):
    text = DEFAULT.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / name
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


def test_load_default() -> None:
    config = load_config(DEFAULT)

    assert config.controller.device_names == (
        "Logitech Gamepad F310",
        "Logitech Gamepad F710",
    )
    assert config.controller.axes[1] == "L_Y"
    assert config.controller.buttons[304] == "A"
    assert config.controller.trigger_mux == {"vertical": ("L_trig", "R_trig")}
    assert config.controller.arm == "A"
    assert config.controller.disarm == "B"
    assert config.controller.reset_flips == "Back"

    assert [motor.name for motor in config.motors] == ["left", "right", "vertical"]
    assert [(motor.rpwm, motor.lpwm) for motor in config.motors] == [(2, 4), (0, 5), (1, 3)]
    assert all(motor.max_duty == 0.5 for motor in config.motors)
    assert all(motor.invert is False for motor in config.motors)

    assert config.pwm_frequency == 1000
    assert config.watchdog_timeout_s == 0.0
    assert config.control_rate_hz == 50.0


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.toml")


def test_fourth_ibt2_motor_from_toml_only(tmp_path: Path) -> None:
    text = DEFAULT.read_text(encoding="utf-8")
    text = text.replace('5 = "R_trig"', '0 = "L_X"\n5 = "R_trig"', 1)
    text += """
[[motor]]
name = "fourth"
rpwm = 6
lpwm = 7
max_duty = 0.5
control = "L_X"
deadzone = true
invert = false
"""
    path = tmp_path / "four.toml"
    path.write_text(text, encoding="utf-8")

    config = load_config(path)

    assert len(config.motors) == 4
    fourth = config.motors[-1]
    assert fourth.name == "fourth"
    assert (fourth.rpwm, fourth.lpwm) == (6, 7)
    assert fourth.control == "L_X"


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("rpwm = 0", "rpwm = 2", "globally unique"),
        ("lpwm = 4", "lpwm = 2", "globally unique"),
        ("lpwm = 4", "lpwm = 16", r"0\.\.15"),
        ("max_duty = 0.5", "max_duty = 0.0", "max_duty"),
        ("max_duty = 0.5", "max_duty = 1.5", "max_duty"),
        ("pwm_frequency = 1000", "pwm_frequency = 23", "pwm_frequency"),
        ("pwm_frequency = 1000", "pwm_frequency = 1527", "pwm_frequency"),
        ('control = "L_Y"', 'control = "bogus"', "unknown control"),
        ('control = "vertical"', 'control = "L_trig"', "consumed by trigger_mux"),
        ('arm = "A"', 'arm = "L_bmp"', "must not collide"),
        ('305 = "B"', '305 = "A"', "button labels must be unique"),
        ('4 = "R_Y"', '4 = "L_Y"', "axis labels must be unique"),
        ("watchdog_timeout_s = 0.0", "watchdog_timeout_s = -0.5", "watchdog"),
    ],
)
def test_invalid_config_is_rejected(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    path = write_variant(tmp_path, old, new)
    with pytest.raises(ValueError, match=match):
        load_config(path)

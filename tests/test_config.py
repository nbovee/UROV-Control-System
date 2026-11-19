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
    controller = config.controller

    assert controller.device_names == (
        "Logitech Gamepad F310",
        "Logitech Gamepad F710",
    )
    assert (controller.axes[1], controller.axes[16], controller.axes[17]) == (
        "L_Y",
        "Dpad_X",
        "Dpad_Y",
    )
    assert controller.buttons[304] == "A"
    assert controller.trigger_mux == {"vertical": ("L_trig", "R_trig")}
    assert (controller.arm, controller.disarm, controller.reset_flips) == ("A", "B", "Back")

    assert [(m.name, m.rpwm, m.lpwm) for m in config.motors] == [
        ("left", 2, 4),
        ("right", 0, 5),
        ("vertical_primary", 1, 3),
        ("vertical_secondary", 6, 7),
    ]
    assert {(m.max_duty, m.invert) for m in config.motors} == {(0.5, False)}

    balance = {group.name: group for group in controller.balance}
    assert {
        (name, group.axis, group.primary, group.secondary, group.step, group.max_trim)
        for name, group in balance.items()
    } == {
        ("horizontal", "Dpad_X", "left", "right", 0.05, 0.30),
        ("vertical", "Dpad_Y", "vertical_primary", "vertical_secondary", 0.05, 0.30),
    }

    assert (config.pwm_frequency, config.watchdog_timeout_s, config.control_rate_hz) == (
        1000,
        0.0,
        50.0,
    )


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.toml")


def test_fifth_ibt2_motor_from_toml_only(tmp_path: Path) -> None:
    text = DEFAULT.read_text(encoding="utf-8")
    text = text.replace('5 = "R_trig"', '0 = "L_X"\n5 = "R_trig"', 1)
    text += """
[[motor]]
name = "fifth"
rpwm = 8
lpwm = 9
max_duty = 0.5
control = "L_X"
deadzone = true
invert = false
"""
    path = tmp_path / "five.toml"
    path.write_text(text, encoding="utf-8")

    config = load_config(path)

    assert len(config.motors) == 5
    fifth = config.motors[-1]
    assert (fifth.name, fifth.rpwm, fifth.lpwm, fifth.control) == ("fifth", 8, 9, "L_X")


INVALID_CONFIG_CASES = [
    # Motor section.
    pytest.param("rpwm = 0", "rpwm = 2", "globally unique", id="duplicate-rpwm"),
    pytest.param("lpwm = 4", "lpwm = 2", "globally unique", id="duplicate-lpwm"),
    pytest.param("lpwm = 4", "lpwm = 16", r"0\.\.15", id="channel-out-of-range"),
    pytest.param("max_duty = 0.5", "max_duty = 0.0", "max_duty", id="max-duty-zero"),
    pytest.param("max_duty = 0.5", "max_duty = 1.5", "max_duty", id="max-duty-too-high"),
    # Vehicle section.
    pytest.param(
        "pwm_frequency = 1000",
        "pwm_frequency = 23",
        "pwm_frequency",
        id="pwm-frequency-too-low",
    ),
    pytest.param(
        "pwm_frequency = 1000",
        "pwm_frequency = 1527",
        "pwm_frequency",
        id="pwm-frequency-too-high",
    ),
    pytest.param(
        "watchdog_timeout_s = 0.0",
        "watchdog_timeout_s = -0.5",
        "watchdog",
        id="watchdog-negative",
    ),
    # Controller section.
    pytest.param(
        'control = "L_Y"',
        'control = "bogus"',
        "unknown control",
        id="motor-control-unknown",
    ),
    pytest.param(
        'control = "vertical"',
        'control = "L_trig"',
        "consumed by trigger_mux",
        id="motor-control-consumed-by-mux",
    ),
    pytest.param(
        'arm = "A"',
        'arm = "L_bmp"',
        "must not collide",
        id="arm-collides-with-flip",
    ),
    pytest.param(
        '305 = "B"',
        '305 = "A"',
        "button labels must be unique",
        id="duplicate-button-label",
    ),
    pytest.param(
        '4 = "R_Y"',
        '4 = "L_Y"',
        "axis labels must be unique",
        id="duplicate-axis-label",
    ),
    # Balance groups.
    pytest.param(
        'axis = "Dpad_X"',
        'axis = "bogus"',
        "is not an axis label",
        id="balance-axis-unknown",
    ),
    pytest.param(
        'axis = "Dpad_X"',
        'axis = "L_trig"',
        "consumed by trigger_mux",
        id="balance-axis-consumed-by-mux",
    ),
    pytest.param(
        'axis = "Dpad_Y"',
        'axis = "Dpad_X"',
        "multiple balance groups",
        id="balance-axis-duplicate",
    ),
    pytest.param(
        'control = "L_Y"',
        'control = "Dpad_X"',
        "reserved for a balance group",
        id="motor-control-reserved-for-balance",
    ),
    pytest.param(
        'primary = "left"',
        'primary = "ghost"',
        "unknown primary motor",
        id="balance-primary-unknown",
    ),
    pytest.param(
        'secondary = "right"',
        'secondary = "ghost"',
        "unknown secondary motor",
        id="balance-secondary-unknown",
    ),
    pytest.param(
        'secondary = "right"',
        'secondary = "left"',
        "must differ",
        id="balance-primary-secondary-same",
    ),
    pytest.param(
        'primary = "vertical_primary"',
        'primary = "left"',
        "multiple balance groups",
        id="balance-motor-in-two-groups",
    ),
    pytest.param("step = 0.05", "step = 0.0", r"0\.0 < step", id="balance-step-zero"),
    pytest.param(
        "step = 0.05",
        "step = 0.40",
        r"0\.0 < step",
        id="balance-step-exceeds-max-trim",
    ),
    pytest.param(
        "max_trim = 0.30",
        "max_trim = 1.0",
        r"0\.0 < step",
        id="balance-max-trim-too-high",
    ),
]


@pytest.mark.parametrize(("old", "new", "match"), INVALID_CONFIG_CASES)
def test_invalid_config_is_rejected(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    path = write_variant(tmp_path, old, new)
    with pytest.raises(ValueError, match=match):
        load_config(path)

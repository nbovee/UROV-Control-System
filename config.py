"""TOML configuration for the UROV."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / "configs" / "default.toml"


@dataclass(frozen=True)
class ControllerConfig:
    device_names: tuple[str, ...]
    axes: dict[int, str]
    buttons: dict[int, str]
    trigger_mux: dict[str, tuple[str, str]]
    arm: str | None = "A"
    disarm: str | None = "B"
    reset_flips: str | None = "Back"
    deadzone: float = 0.02
    reconnect_interval_s: float = 0.5


@dataclass(frozen=True)
class MotorConfig:
    name: str
    rpwm: int
    lpwm: int
    control: str
    max_duty: float = 0.5
    flip: str | None = None
    deadzone: bool = False
    invert: bool = False


@dataclass(frozen=True)
class Config:
    controller: ControllerConfig
    motors: tuple[MotorConfig, ...]
    i2c_bus: int = 1
    pca9685_address: int = 0x40
    pwm_frequency: int = 1000
    control_rate_hz: float = 50.0
    watchdog_timeout_s: float = 0.0


def _as_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc


def _as_float(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc


def _label_map(section: object, field: str) -> dict[int, str]:
    if not isinstance(section, dict):
        raise ValueError(f"controller.{field} must be a table of codes to labels")
    labels: dict[int, str] = {}
    for raw_code, raw_label in section.items():
        code = _as_int(raw_code, f"controller.{field} code {raw_code!r}")
        if not isinstance(raw_label, str) or not raw_label:
            raise ValueError(f"controller.{field}: labels must be non-empty strings")
        labels[code] = raw_label
    return labels


def _trigger_mux(section: object) -> dict[str, tuple[str, str]]:
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError("controller.trigger_mux must be a table")
    mux: dict[str, tuple[str, str]] = {}
    for raw_key, raw_value in section.items():
        key = str(raw_key)
        if not key:
            raise ValueError("controller.trigger_mux keys must be non-empty")
        if not isinstance(raw_value, list) or len(raw_value) != 2:
            raise ValueError(f"controller.trigger_mux.{key} must be a 2-item list")
        pos, neg = raw_value
        if not isinstance(pos, str) or not isinstance(neg, str) or not pos or not neg:
            raise ValueError(f"controller.trigger_mux.{key} labels must be non-empty strings")
        mux[key] = (pos, neg)
    return mux


def _optional_label(section: dict, field: str, default: str) -> str | None:
    value = section.get(field, default)
    return None if value is None else str(value)


def _motor(section: object) -> MotorConfig:
    if not isinstance(section, dict):
        raise ValueError("each [[motor]] entry must be a table")
    missing = [key for key in ("name", "rpwm", "lpwm", "control") if key not in section]
    if missing:
        raise ValueError(f"[[motor]] is missing required field(s): {', '.join(missing)}")
    name = str(section["name"])
    return MotorConfig(
        name=name,
        rpwm=_as_int(section["rpwm"], f"motor {name!r}.rpwm"),
        lpwm=_as_int(section["lpwm"], f"motor {name!r}.lpwm"),
        control=str(section["control"]),
        max_duty=_as_float(section.get("max_duty", 0.5), f"motor {name!r}.max_duty"),
        flip=str(section["flip"]) if "flip" in section else None,
        deadzone=bool(section.get("deadzone", False)),
        invert=bool(section.get("invert", False)),
    )


def _validate(config: Config) -> None:
    controller = config.controller
    motors = config.motors

    names = [motor.name for motor in motors]
    if not names:
        raise ValueError("at least one [[motor]] is required")
    if any(not name for name in names):
        raise ValueError("motor names must be non-empty")
    if len(set(names)) != len(names):
        raise ValueError("motor names must be unique")

    channels: list[int] = []
    for motor in motors:
        for channel in (motor.rpwm, motor.lpwm):
            if not 0 <= channel <= 15:
                raise ValueError(f"motor {motor.name}: PWM channel {channel} must be in 0..15")
            channels.append(channel)
        if not 0.0 < motor.max_duty <= 1.0:
            raise ValueError(f"motor {motor.name}: max_duty must be in (0.0, 1.0]")
        if not motor.control:
            raise ValueError(f"motor {motor.name}: control must be non-empty")
    if len(set(channels)) != len(channels):
        raise ValueError("motor PWM channels must be globally unique")

    axis_labels = list(controller.axes.values())
    button_labels = list(controller.buttons.values())
    if len(set(axis_labels)) != len(axis_labels):
        raise ValueError("controller axis labels must be unique")
    if len(set(button_labels)) != len(button_labels):
        raise ValueError("controller button labels must be unique")
    if set(axis_labels) & set(button_labels):
        raise ValueError("controller axis and button labels must be disjoint")

    consumed_triggers: set[str] = set()
    for key, (pos, neg) in controller.trigger_mux.items():
        if key in axis_labels or key in button_labels:
            raise ValueError(f"trigger_mux key {key!r} collides with an axis or button label")
        if pos == neg:
            raise ValueError(f"trigger_mux.{key}: positive and negative labels must differ")
        for label in (pos, neg):
            if label not in axis_labels:
                raise ValueError(f"trigger_mux.{key}: {label!r} is not an axis label")
        consumed_triggers.update((pos, neg))

    for motor in motors:
        if motor.control in consumed_triggers:
            raise ValueError(
                f"motor {motor.name}: trigger {motor.control!r} is consumed by trigger_mux"
            )

    available_controls = (set(axis_labels) - consumed_triggers) | set(controller.trigger_mux)
    for motor in motors:
        if motor.control not in available_controls:
            raise ValueError(f"motor {motor.name}: unknown control {motor.control!r}")
        if motor.flip is not None and motor.flip not in button_labels:
            raise ValueError(f"motor {motor.name}: unknown flip control {motor.flip!r}")

    actions = {
        "arm": controller.arm,
        "disarm": controller.disarm,
        "reset_flips": controller.reset_flips,
    }
    for name, label in actions.items():
        if label is not None and label not in button_labels:
            raise ValueError(f"controller.{name}: unknown button {label!r}")
    action_labels = {label for label in actions.values() if label is not None}
    if len(action_labels) != sum(label is not None for label in actions.values()):
        raise ValueError("controller arm, disarm, and reset_flips must be distinct")
    flips = {motor.flip for motor in motors if motor.flip is not None}
    if action_labels & flips:
        raise ValueError(
            "controller arm, disarm, and reset_flips must not collide with motor flips"
        )

    if config.i2c_bus < 0:
        raise ValueError("vehicle.i2c_bus must be non-negative")
    if not 0x03 <= config.pca9685_address <= 0x77:
        raise ValueError("vehicle.pca9685_address must be in 0x03..0x77")
    if not 24 <= config.pwm_frequency <= 1526:
        raise ValueError("vehicle.pwm_frequency must be in 24..1526")
    if config.control_rate_hz <= 0:
        raise ValueError("vehicle.control_rate_hz must be positive")
    if config.watchdog_timeout_s < 0:
        raise ValueError("vehicle.watchdog_timeout_s must be non-negative")
    if not 0 <= controller.deadzone < 1:
        raise ValueError("controller.deadzone must be in [0, 1)")
    if controller.reconnect_interval_s <= 0:
        raise ValueError("controller.reconnect_interval_s must be positive")


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate a vehicle TOML file."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    raw_controller = data.get("controller")
    if not isinstance(raw_controller, dict):
        raise ValueError("missing [controller] table")
    raw_vehicle = data.get("vehicle", {})
    if not isinstance(raw_vehicle, dict):
        raise ValueError("[vehicle] must be a table")
    raw_motors = data.get("motor")
    if not isinstance(raw_motors, list) or not raw_motors:
        raise ValueError("at least one [[motor]] table is required")

    raw_device_names = raw_controller.get("device_names")
    if not isinstance(raw_device_names, list) or not raw_device_names:
        raise ValueError("controller.device_names must be a non-empty list of strings")
    if not all(isinstance(name, str) and name for name in raw_device_names):
        raise ValueError("controller.device_names must contain only non-empty strings")

    raw_actions = raw_controller.get("actions", {})
    if not isinstance(raw_actions, dict):
        raise ValueError("controller.actions must be a table")

    controller = ControllerConfig(
        device_names=tuple(raw_device_names),
        axes=_label_map(raw_controller.get("axes"), "axes"),
        buttons=_label_map(raw_controller.get("buttons"), "buttons"),
        trigger_mux=_trigger_mux(raw_controller.get("trigger_mux")),
        arm=_optional_label(raw_actions, "arm", "A"),
        disarm=_optional_label(raw_actions, "disarm", "B"),
        reset_flips=_optional_label(raw_actions, "reset_flips", "Back"),
        deadzone=_as_float(raw_controller.get("deadzone", 0.02), "controller.deadzone"),
        reconnect_interval_s=_as_float(
            raw_controller.get("reconnect_interval_s", 0.5),
            "controller.reconnect_interval_s",
        ),
    )
    config = Config(
        controller=controller,
        motors=tuple(_motor(section) for section in raw_motors),
        i2c_bus=_as_int(raw_vehicle.get("i2c_bus", 1), "vehicle.i2c_bus"),
        pca9685_address=_as_int(
            raw_vehicle.get("pca9685_address", 0x40), "vehicle.pca9685_address"
        ),
        pwm_frequency=_as_int(raw_vehicle.get("pwm_frequency", 1000), "vehicle.pwm_frequency"),
        control_rate_hz=_as_float(
            raw_vehicle.get("control_rate_hz", 50.0), "vehicle.control_rate_hz"
        ),
        watchdog_timeout_s=_as_float(
            raw_vehicle.get("watchdog_timeout_s", 0.0), "vehicle.watchdog_timeout_s"
        ),
    )
    _validate(config)
    return config

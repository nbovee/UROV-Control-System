"""Input normalization/muxing and hotplug-capable evdev controller access."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config import Config, ControllerConfig

EV_KEY = 1
EV_ABS = 3

logger = logging.getLogger(__name__)


class ControllerDisconnected(OSError):
    """Raised when an active evdev controller disconnects."""


def normalize_axis(value: float, minimum: float, maximum: float) -> float:
    """Normalize a raw axis value to [-1.0, 1.0] (or [0.0, 1.0] for triggers)."""
    if maximum <= minimum:
        return 0.0
    if minimum < 0 < maximum:
        norm = value / maximum if value >= 0 else value / abs(minimum)
        return max(-1.0, min(1.0, norm))
    if minimum >= 0:
        norm = (value - minimum) / (maximum - minimum)
        return max(0.0, min(1.0, norm))
    norm = 2.0 * (value - minimum) / (maximum - minimum) - 1.0
    return max(-1.0, min(1.0, norm))


def apply_deadzone(value: float, deadzone: float) -> float:
    """Zero small inputs and rescale the remaining range smoothly."""
    if deadzone <= 0.0:
        return max(-1.0, min(1.0, value))
    magnitude = abs(value)
    if magnitude < deadzone:
        return 0.0
    if deadzone >= 1.0:
        return 0.0
    scaled = min(1.0, (magnitude - deadzone) / (1.0 - deadzone))
    return scaled if value >= 0 else -scaled


class InputMapper:
    """Translate raw evdev events into normalized control setpoints."""

    def __init__(self, config: Config) -> None:
        self.config = config
        controller = config.controller

        self._trigger_labels: set[str] = set()
        for positive, negative in controller.trigger_mux.values():
            self._trigger_labels.update((positive, negative))

        self._axes: dict[str, float] = {label: 0.0 for label in controller.axes.values()}
        self._controls: dict[str, float] = {
            label: 0.0 for label in controller.axes.values() if label not in self._trigger_labels
        }
        self._controls.update({mux: 0.0 for mux in controller.trigger_mux})

        self.armed: bool = controller.arm is None
        self._flips: dict[str, bool] = {motor.name: False for motor in config.motors}
        self._by_flip: dict[str, list[str]] = {}
        for motor in config.motors:
            if motor.flip is not None:
                self._by_flip.setdefault(motor.flip, []).append(motor.name)

    @property
    def controls(self) -> dict[str, float]:
        """Return a copy of the current normalized controls."""
        return dict(self._controls)

    def is_neutral(self) -> bool:
        """Return True when every motor control is within the neutral margin."""
        margin = max(self.config.controller.deadzone, 0.02)
        return all(
            abs(self._controls.get(motor.control, 0.0)) <= margin for motor in self.config.motors
        )

    def process_event(
        self,
        event_type: int,
        code: int,
        value: int,
        absinfo: Any = None,
    ) -> bool:
        """Apply one raw event. Return True when it matched a binding."""
        controller = self.config.controller

        if event_type == EV_ABS and code in controller.axes:
            label = controller.axes[code]
            if absinfo is not None:
                minimum, maximum = absinfo.min, absinfo.max
            elif label in self._trigger_labels:
                minimum, maximum = 0, 255
            else:
                minimum, maximum = -32768, 32767

            self._axes[label] = normalize_axis(value, minimum, maximum)
            if label in self._trigger_labels:
                for mux_name, (positive, negative) in controller.trigger_mux.items():
                    mux_value = max(-1.0, min(1.0, self._axes[positive] - self._axes[negative]))
                    self._controls[mux_name] = mux_value
            else:
                self._controls[label] = max(-1.0, min(1.0, self._axes[label]))
            return True

        if event_type == EV_KEY and code in controller.buttons:
            label = controller.buttons[code]
            if value == 1:
                if label == controller.disarm:
                    if self.armed:
                        logger.info("Disarmed (E-Stop).")
                    self.armed = False
                elif label == controller.arm:
                    if not self.armed:
                        if self.is_neutral():
                            self.armed = True
                            logger.info("Armed.")
                        else:
                            logger.warning("Cannot arm: controls are not neutral.")
                elif label == controller.reset_flips:
                    for name in self._flips:
                        self._flips[name] = False
                    logger.info("Motor flips reset.")
                elif label in self._by_flip:
                    for name in self._by_flip[label]:
                        self._flips[name] = not self._flips[name]
            return True

        return False

    def reset(self, *, disarm: bool = True) -> None:
        """Zero all axes and controls, optionally disarming."""
        for label in self._axes:
            self._axes[label] = 0.0
        for control in self._controls:
            self._controls[control] = 0.0
        if disarm and self.config.controller.arm is not None:
            self.armed = False

    def motor_setpoints(self) -> dict[str, float]:
        """Return normalized setpoints for every motor, zeroed while disarmed."""
        if not self.armed:
            return {motor.name: 0.0 for motor in self.config.motors}

        setpoints: dict[str, float] = {}
        for motor in self.config.motors:
            value = self._controls.get(motor.control, 0.0)
            if motor.deadzone:
                value = apply_deadzone(value, self.config.controller.deadzone)
            if self._flips[motor.name]:
                value = -value
            setpoints[motor.name] = max(-1.0, min(1.0, value))
        return setpoints


class Controller:
    """Discover, grab, and read a named evdev controller with hotplug support."""

    def __init__(self, config: ControllerConfig | Config, evdev_mod: Any = None) -> None:
        self.config = config
        self.device: Any = None
        self._evdev = evdev_mod
        self._absinfo: dict[int, object] = {}

    @property
    def connected(self) -> bool:
        return self.device is not None

    def _get_evdev(self) -> Any:
        if self._evdev is None:
            import evdev

            self._evdev = evdev
        return self._evdev

    def _device_config(self) -> ControllerConfig:
        if hasattr(self.config, "device_names") and hasattr(self.config, "axes"):
            return self.config
        return self.config.controller

    def connect(self) -> bool:
        """Scan for a configured device and grab it, continuing past failures."""
        if self.device is not None:
            return True

        ev = self._get_evdev()
        device_config = self._device_config()
        for path in ev.list_devices():
            try:
                device = ev.InputDevice(path)
            except OSError:
                continue
            if device.name not in device_config.device_names:
                device.close()
                continue
            try:
                device.grab()
            except OSError:
                logger.warning("Could not grab %s; skipping.", device.name)
                device.close()
                continue

            self.device = device
            for code in device_config.axes:
                with contextlib.suppress(OSError, AttributeError):
                    self._absinfo[code] = device.absinfo(code)
            logger.info("%s connected (%s).", device.name, getattr(device, "path", "unknown"))
            return True

        return False

    def absinfo(self, code: int) -> object | None:
        return self._absinfo.get(code)

    async def events(self):
        """Yield evdev events asynchronously until disconnect or cancellation."""
        if self.device is None:
            return
        stream = self.device.async_read_loop()
        try:
            async for event in stream:
                yield event
        except OSError as exc:
            raise ControllerDisconnected("Controller read failed") from exc
        finally:
            with contextlib.suppress(OSError, RuntimeError):
                await stream.aclose()

    def disconnect(self) -> None:
        """Release and close the device, if any."""
        if self.device is None:
            return
        with contextlib.suppress(OSError):
            self.device.ungrab()
        with contextlib.suppress(OSError):
            self.device.close()
        self.device = None
        self._absinfo.clear()
        logger.info("Controller disconnected.")

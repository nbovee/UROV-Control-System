"""Hardware actuation layer for the IBT-2 thruster set."""

from __future__ import annotations

import logging

from motor.motor import IBT2Motor
from motor.pca9685 import PCA9685

logger = logging.getLogger(__name__)


class MotorSystem:
    """Own the PCA9685 and apply named throttle setpoints to IBT-2 motors."""

    def __init__(self, config, pca=None) -> None:
        self.config = config
        self._owns_pca = pca is None
        if pca is None:
            pca = PCA9685(config.i2c_bus, config.pca9685_address, config.pwm_frequency)
        self._pca = pca
        self._motors: dict[str, IBT2Motor] = {}

        try:
            for entry in config.motors:
                self._motors[entry.name] = IBT2Motor(
                    pca,
                    entry.rpwm,
                    entry.lpwm,
                    entry.max_duty,
                    entry.invert,
                )
            self.stop_all()
        except Exception:
            if self._owns_pca:
                try:
                    pca.close()
                except Exception:
                    logger.exception("failed to close PCA9685 after setup error")
            raise

        self._cleaned = False

    @property
    def motor_names(self) -> tuple[str, ...]:
        """Names of the configured motors."""
        return tuple(self._motors.keys())

    def apply(self, setpoints: dict[str, float]) -> None:
        """Apply throttle setpoints; motors with no entry are stopped."""
        for name, motor in self._motors.items():
            motor.output(setpoints.get(name, 0.0))

    def stop_all(self) -> None:
        """Stop every motor, continuing past individual failures."""
        try:
            self._pca.all_off()
        except Exception:
            logger.exception("failed to turn off all PCA9685 channels")
        for motor in self._motors.values():
            try:
                motor.stop()
            except Exception:
                logger.exception("failed to stop motor")

    def cleanup(self) -> None:
        """Stop all outputs and release the PCA9685 once."""
        if self._cleaned:
            return
        self._cleaned = True
        self.stop_all()
        try:
            close = getattr(self._pca, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.exception("failed to close PCA9685")

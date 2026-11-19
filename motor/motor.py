"""IBT-2 (BTS7960) dual-PWM motor driver."""

from __future__ import annotations


class IBT2Motor:
    """One IBT-2 half-bridge driven by two PCA9685 PWM channels.

    On the IBT-2 carrier PCB, R_EN and L_EN are tied HIGH, so only R_PWM and
    L_PWM are software controlled. Exactly one input is driven at a time; the
    inactive input is zeroed first to prevent shoot-through.
    """

    def __init__(
        self,
        pca,
        rpwm: int,
        lpwm: int,
        max_duty: float = 0.5,
        invert: bool = False,
    ) -> None:
        if not 0 <= rpwm <= 15:
            raise ValueError(f"rpwm must be between 0 and 15, got {rpwm}")
        if not 0 <= lpwm <= 15:
            raise ValueError(f"lpwm must be between 0 and 15, got {lpwm}")
        if rpwm == lpwm:
            raise ValueError("rpwm and lpwm must use different channels")
        if not 0.0 < max_duty <= 1.0:
            raise ValueError(f"max_duty must be in (0.0, 1.0], got {max_duty}")

        self.pca = pca
        self.rpwm = rpwm
        self.lpwm = lpwm
        self.max_duty = max_duty
        self.invert = invert

    def output(self, throttle: float) -> None:
        """Drive the motor from a signed throttle in the range -1.0 to 1.0."""
        throttle = max(-1.0, min(1.0, throttle))
        if self.invert:
            throttle = -throttle

        duty = abs(throttle) * self.max_duty
        if duty == 0.0:
            self.stop()
            return

        if throttle > 0:
            self.pca.set_duty(self.lpwm, 0.0)
            self.pca.set_duty(self.rpwm, duty)
        else:
            self.pca.set_duty(self.rpwm, 0.0)
            self.pca.set_duty(self.lpwm, duty)

    def stop(self) -> None:
        """Zero both PWM inputs."""
        self.pca.set_duty(self.rpwm, 0.0)
        self.pca.set_duty(self.lpwm, 0.0)

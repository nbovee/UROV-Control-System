"""Minimal PCA9685 16-channel PWM driver built directly on smbus2."""

from __future__ import annotations

import logging
import time

MODE1 = 0x00
MODE2 = 0x01
PRESCALE = 0xFE
LED0_ON_L = 0x06
ALL_LED_OFF_H = 0xFD

MODE1_RESTART = 0x80
MODE1_AI = 0x20
MODE1_SLEEP = 0x10

MODE2_OUTDRV = 0x04

OSC_CLOCK_HZ = 25_000_000
PWM_STEPS = 4095

logger = logging.getLogger(__name__)


class PCA9685:
    """PCA9685 PWM controller driven over I2C with smbus2 only."""

    def __init__(
        self,
        bus_num: int = 1,
        address: int = 0x40,
        frequency: int = 1000,
        bus=None,
        sleep=time.sleep,
    ) -> None:
        self.address = address
        self._sleep = sleep
        self._closed = False

        if bus is None:
            from smbus2 import SMBus

            bus = SMBus(bus_num)
            self._owns_bus = True
        else:
            self._owns_bus = False
        self._bus = bus

        try:
            self._bus.write_byte_data(self.address, MODE2, MODE2_OUTDRV)
            self._bus.write_byte_data(self.address, MODE1, MODE1_AI)
            self.all_off()
            self.set_pwm_frequency(frequency)
        except Exception:
            if self._owns_bus:
                try:
                    self._bus.close()
                except Exception:
                    logger.exception("failed to close SMBus after setup error")
            raise

    def set_pwm_frequency(self, frequency: int) -> None:
        """Set the PWM output frequency in hertz."""
        if not 24 <= frequency <= 1526:
            raise ValueError(f"frequency must be between 24 and 1526 Hz, got {frequency}")
        prescale = max(3, min(255, round(OSC_CLOCK_HZ / (4096 * frequency)) - 1))

        old_mode = self._bus.read_byte_data(self.address, MODE1)
        self._bus.write_byte_data(
            self.address, MODE1, (old_mode & ~MODE1_RESTART) | MODE1_SLEEP
        )
        self._bus.write_byte_data(self.address, PRESCALE, prescale)
        self._bus.write_byte_data(
            self.address, MODE1, (old_mode & ~MODE1_SLEEP) | MODE1_AI
        )
        self._sleep(0.005)
        self._bus.write_byte_data(
            self.address, MODE1, (old_mode & ~MODE1_SLEEP) | MODE1_AI | MODE1_RESTART
        )

    def set_duty(self, channel: int, duty: float) -> None:
        """Set one channel's duty cycle, clamped to the range 0.0 to 1.0."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be between 0 and 15, got {channel}")
        duty = max(0.0, min(1.0, duty))
        counts = round(duty * PWM_STEPS)
        reg = LED0_ON_L + 4 * channel

        if counts <= 0:
            data = [0, 0, 0, 0x10]
        elif counts >= PWM_STEPS:
            data = [0, 0x10, 0, 0]
        else:
            data = [0, 0, counts & 0xFF, (counts >> 8) & 0x0F]

        self._bus.write_i2c_block_data(self.address, reg, data)

    def all_off(self) -> None:
        """Turn off all 16 channels atomically."""
        self._bus.write_byte_data(self.address, ALL_LED_OFF_H, 0x10)

    def close(self) -> None:
        """Stop output, put the device to sleep, and release an owned bus."""
        if self._closed:
            return
        self._closed = True

        try:
            self.all_off()
        except Exception:
            logger.exception("failed to turn off PCA9685 outputs")
        try:
            self._bus.write_byte_data(self.address, MODE1, MODE1_SLEEP)
        except Exception:
            logger.exception("failed to put PCA9685 to sleep")
        if self._owns_bus:
            try:
                self._bus.close()
            except Exception:
                logger.exception("failed to close SMBus")

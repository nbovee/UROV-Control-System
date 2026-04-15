import RPi.GPIO as GPIO
import busio
from board import SCL, SDA
from adafruit_pca9685 import PCA9685
from motor.motor import Motor, Motor_IBT2


class MotorSystem:
    def __init__(self):
        GPIO.setmode(GPIO.BCM)

        i2c_bus = busio.I2C(SCL, SDA)
        pca = PCA9685(i2c_bus)
        pca.frequency = 60

        self.l_motor = Motor(GPIO, pca, 18, 17, 2, 2**15, 0)
        self.r_motor = Motor(GPIO, pca, 23, 24, 0, 2**15, 0)
        self.v_motor = Motor_IBT2(GPIO, pca, 5, 6, 1, 3, 2**8, -1 * 2**8)

        self._gpio = GPIO
        self._l_trig_last = 0
        self._r_trig_last = 0

        self.handlers = {
            "L_Y": self._drive_left,
            "R_Y": self._drive_right,
            "L_trig": self._drive_vertical,
            "R_trig": self._drive_vertical,
            "L_bmp": self._flip_left,
            "R_bmp": self._flip_right,
            "Y": self._flip_vertical,
            "Start": self._reset_flips,
        }

    def _drive_left(self, value, label):
        print(self.l_motor.output(value))

    def _drive_right(self, value, label):
        self.r_motor.output(value)

    def _drive_vertical(self, value, label):
        if label == "L_trig":
            self._l_trig_last = value
            pulse = self.v_motor.output(value - self._r_trig_last)
        else:
            self._r_trig_last = value
            pulse = self.v_motor.output(self._l_trig_last - value)
        return pulse

    def _flip_left(self, value, label):
        if value == 1:
            self.l_motor.flip_stick()

    def _flip_right(self, value, label):
        if value == 1:
            self.r_motor.flip_stick()

    def _flip_vertical(self, value, label):
        if value == 1:
            self.v_motor.flip_stick()

    def _reset_flips(self, value, label):
        if value == 1:
            self.l_motor.reset_flip()
            self.r_motor.reset_flip()
            self.v_motor.reset_flip()

    def cleanup(self):
        self._gpio.cleanup()

#!/usr/bin/python3

import busio
from board import SCL, SDA
from adafruit_pca9685 import PCA9685


class PWMHandler:
    i2c_bus = busio.I2C(SCL, SDA)

    def __init__(self):
        self.max_resolution = 2 ** 15 - 1
        self.pca = PCA9685(PWMHandler.i2c_bus)
        self.pca.frequency = 60

    def set_output(self, duty_cycle, channel):
        self.pca.channels[channel].duty_cycle = duty_cycle * self.max_resolution

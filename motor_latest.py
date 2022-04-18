class Motor:

    def __init__(self, io_object, pca_object, pin0, pin1, pwm0, max_val, min_val):
        # init internal variables
        self.motorGPIO = io_object
        self.motorI2C = pca_object
        self.pin0GPIO = pin0
        self.pin1GPIO = pin1
        self.pin0PWM = pwm0
        self.maxThrottle = max_val
        self.minThrottle = min_val
        self.minPulse = 0
        self.maxPulse = 2**16 -1 # 2000
        # centerPulse = (maxPulse + minPulse) / 2 #would be used for if pwm signal alone could handle directionality
        self.rangeThrottle = self.maxThrottle  # - self.minThrottle
        self.rangeOutput = (self.maxPulse - self.minPulse)

        # holders for control logic
        self.sign = False
        self.pulse = 0
        # self.lastPulse = 0
        self.invert = False

        # initialize pins to control motor direction
        self.motorGPIO.setup(self.pin0GPIO, self.motorGPIO.OUT)
        self.motorGPIO.setup(self.pin1GPIO, self.motorGPIO.OUT)

    def output(self, throttle):
        # handles motor control, assuming bidirectional throttle and L298N style motor controller
        self.sign = False if throttle < 0 else True
        # self.sign = throttle/abs(throttle) if throttle != 0 else 1
        # self.lastPulse = self.pulse
        self.pulse = self.scale_throttle(abs(throttle))
        self.set_gpio_pins()
        self.motorI2C.channels[self.pin0PWM].duty_cycle = self.pulse
        return int(self.pulse)


    def scale_throttle(self, throttle):
        # scales throttle to pwm pulse range, assuming a monodirectional throttle, directionality is handled by output()
        return int(abs(throttle) * self.rangeOutput / self.rangeThrottle + self.minPulse)

    def set_gpio_pins(self):
        if not (self.invert ^ self.sign):
            self.motorGPIO.output(self.pin0GPIO, self.motorGPIO.HIGH)
            self.motorGPIO.output(self.pin1GPIO, self.motorGPIO.LOW)
        else:
            self.motorGPIO.output(self.pin0GPIO, self.motorGPIO.LOW)
            self.motorGPIO.output(self.pin1GPIO, self.motorGPIO.HIGH)

    def flip_stick(self):
        self.invert = False if self.invert else True

    def reset_flip(self):
        self.invert = False


class Motor_IBT2(Motor):

    def __init__(self, io_object, pca_object, pin0, pin1, pwm0, pwm1, max_val, min_val):
        super().__init__(io_object, pca_object, pin0, pin1, pwm0, max_val, min_val)
        self.pin1PWM = pwm1

    def output(self, throttle):
        # handles motor control, assuming bidirectional throttle and L298N style motor controller
        self.sign = False if throttle < 0 else True
        # self.sign = throttle/abs(throttle) if throttle != 0 else 1
        # self.lastPulse = self.pulse
        self.pulse = self.scale_throttle(abs(throttle))
        self.set_gpio_pins()
        if self.invert ^ self.sign:
            self.motorI2C.channels[self.pin0PWM].duty_cycle = self.pulse
            self.motorI2C.channels[self.pin1PWM].duty_cycle = 0

        else:
            self.motorI2C.channels[self.pin0PWM].duty_cycle = 0
            self.motorI2C.channels[self.pin1PWM].duty_cycle = self.pulse
        return int(self.pulse)

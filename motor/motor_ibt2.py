class Motor_IBT2:

    def __init__(self, io_object, pca_object, pwm0, pwm1, max_val, min_val):
        # init internal variables
        self.motorGPIO = io_object
        self.motorI2C = pca_object
        self.pin0PWM = pwm0
        self.pin1PWM = pwm1
        self.maxThrottle = max_val
        self.minThrottle = min_val
        self.minPulse = 0
        self.maxPulse = 2000
        # centerPulse = (maxPulse + minPulse) / 2 #would be used for if pwm signal alone could handle directionality
        self.rangeThrottle = self.maxThrottle  # - self.minThrottle
        self.rangeOutput = (self.maxPulse - self.minPulse)

        # holders for control logic
        self.sign = False
        self.pulse = 0
        self.invert = False

    def output(self, throttle):
        # handles motor control, assuming bidirectional throttle and L298N style motor controller
        self.sign = False if throttle < 0 else True
        self.pulse = self.scale_throttle(abs(throttle))
        if self.invert ^ self.sign:
            self.motorI2C.channels[self.pin0PWM].duty_cycle = self.pulse
            self.motorI2C.channels[self.pin1PWM].duty_cycle = 0

        else:
            self.motorI2C.channels[self.pin0PWM].duty_cycle = 0
            self.motorI2C.channels[self.pin1PWM].duty_cycle = self.pulse

    def scale_throttle(self, throttle):
        # scales throttle to pwm pulse range, assuming a monodirectional throttle, directionality is handled by output()
        return abs(throttle) * self.rangeOutput / self.rangeThrottle + self.minPulse

    def flip_stick(self):
        self.invert = False if self.invert else True

    def reset_flip(self):
        self.invert = False


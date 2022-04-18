from pwm_handler.pwm_handler import PWMHandler
from gpio.gpio import single_gpio


class Motor:

    def __init__(self, pin0, pin1, pwm0, max_val, min_val):
        # init internal variables
        self.pwm = PWMHandler()
        self.motorGPIO = single_gpio()
        self.gpio_pins = [pin0, pin1]
        self.pin0PWM = pwm0
        self.maxThrottle = max_val
        self.minThrottle = min_val
        self.minPulse = 0
        self.maxPulse = 2000  # change to percentile
        # centerPulse = (maxPulse + minPulse) / 2 #would be used for if pwm signal alone could handle directionality
        self.rangeThrottle = self.maxThrottle  # - self.minThrottle
        self.rangeOutput = (self.maxPulse - self.minPulse)

        # holders for control logic
        self.sign = False
        self.pulse = 0
        # self.lastPulse = 0
        self.invert = False

        # initialize pins to control motor direction
        self.motorGPIO.setup(self.gpio_pins[0], self.motorGPIO.OUT)
        self.motorGPIO.setup(self.gpio_pins[1], self.motorGPIO.OUT)

    def output(self, throttle):
        # handles motor control, assuming bidirectional throttle and L298N style motor controller
        self.sign = False if throttle < 0 else True
        # self.sign = throttle/abs(throttle) if throttle != 0 else 1
        # self.lastPulse = self.pulse
        self.pulse = self.scale_throttle(abs(throttle))
        self.set_gpio_pins()
        self.pwm.set_output(self.pulse, self.pin0PWM)

    def scale_throttle(self, throttle):
        # scales throttle to pwm pulse range, assuming a monodirectional throttle, directionality is handled by output()
        return abs(throttle) * self.rangeOutput / self.rangeThrottle + self.minPulse

    def set_gpio_pins(self):
        if not (self.invert ^ self.sign):
            self.motorGPIO.output(self.gpio_pins[0], self.motorGPIO.HIGH)
            self.motorGPIO.output(self.gpio_pins[1], self.motorGPIO.LOW)
        else:
            self.motorGPIO.output(self.gpio_pins[0], self.motorGPIO.LOW)
            self.motorGPIO.output(self.gpio_pins[1], self.motorGPIO.HIGH)

    def flip_stick(self):
        self.invert = False if self.invert else True

    def reset_flip(self):
        self.invert = False

    def __del__(self):
        for pin in self.gpio_pins:
            self.motorGPIO.cleanup(pin.id)
            # self.motorGPIO.setup(pin, self.motorGPIO.IN)


class Motor_IBT2(Motor):

    def __init__(self, pin0, pin1, pwm0, pwm1, max_val, min_val):
        super().__init__(pin0, pin1, pwm0, max_val, min_val)
        self.pin1PWM = pwm1

    def output(self, throttle):
        # handles motor control, assuming bidirectional throttle and L298N style motor controller
        self.sign = False if throttle < 0 else True
        # self.sign = throttle/abs(throttle) if throttle != 0 else 1
        # self.lastPulse = self.pulse
        self.pulse = self.scale_throttle(abs(throttle))
        self.set_gpio_pins()
        if self.invert ^ self.sign:
            self.pwm.set_output(self.pulse, self.pin0PWM)
            self.pwm.set_output(0, self.pin1PWM)

        else:
            self.pwm.set_output(0, self.pin0PWM)
            self.pwm.set_output(self.pulse, self.pin1PWM)

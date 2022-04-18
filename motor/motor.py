from pwm_handler.pwm_handler import PWMHandler
from gpio.gpio import single_gpio


class Motor:

    def __init__(self, pin0, pin1, pwm0):
        # init internal variables
        self.pwm = PWMHandler()
        self.motorGPIO = single_gpio()
        self.gpio_pins = [pin0, pin1]
        self.pin0PWM = pwm0
        # holders for control logic
        self.sign = False
        # self.lastPulse = 0
        self.invert = False

        # initialize pins to control motor direction
        self.motorGPIO.setup(self.gpio_pins[0], self.motorGPIO.OUT)
        self.motorGPIO.setup(self.gpio_pins[1], self.motorGPIO.OUT)

    def output(self, control_value):
        # handles motor control, assuming bidirectional throttle and L298N style motor controller
        self.sign = False if control_value < 0 else True
        duty_cycle = abs(control_value)
        # self.sign = throttle/abs(throttle) if throttle != 0 else 1
        # self.lastPulse = self.pulse
        # self.scale_throttle(abs(throttle))
        self.set_gpio_pins()
        self.pwm.set_output(duty_cycle, self.pin0PWM)

    # def scale_throttle(self, throttle):
    #     # scales throttle to pwm pulse range, assuming a monodirectional throttle, directionality is handled by output()
    #     return abs(throttle) * self.rangeOutput / self.rangeThrottle + self.minPulse

    def set_gpio_pins(self):
        if not (self.invert ^ self.sign):
            self.motorGPIO.output(self.gpio_pins[0])
            self.motorGPIO.output(self.gpio_pins[1])
        else:
            self.motorGPIO.output(self.gpio_pins[0])
            self.motorGPIO.output(self.gpio_pins[1])

    def flip_stick(self):
        self.invert = False if self.invert else True

    def reset_flip(self):
        self.invert = False

    def __del__(self):
        for pin in self.gpio_pins:
            self.motorGPIO.cleanup(pin.id)
            # self.motorGPIO.setup(pin, self.motorGPIO.IN)


class Motor_IBT2(Motor):

    def __init__(self, pin0, pin1, pwm0, pwm1):
        super().__init__(pin0, pin1, pwm0)
        self.pin1PWM = pwm1

    def output(self, control_value):
        # handles motor control, assuming bidirectional throttle from -1 to 1
        self.sign = False if control_value < 0 else True
        duty_cycle = abs(control_value)
        self.set_gpio_pins()
        if self.invert ^ self.sign:
            self.pwm.set_output(duty_cycle, self.pin0PWM)
            self.pwm.set_output(0, self.pin1PWM)

        else:
            self.pwm.set_output(0, self.pin0PWM)
            self.pwm.set_output(duty_cycle, self.pin1PWM)

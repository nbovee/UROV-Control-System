from motor.motor import Motor, Motor_IBT2


class Motors:
    def __init__(self):
        # gpio0, gpio1, pwm0
        self.l_motor = Motor(18, 17, 2)
        self.r_motor = Motor(23, 24, 0)
        # gpio0, gpio1, pwm0, pwm1
        self.v_motor = Motor_IBT2(5, 6, 1, 3)

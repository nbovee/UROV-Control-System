from motor.motor import Motor, Motor_IBT2


class Motors:
    def __init__(self):
        self.l_motor = Motor(18, 17, 2, 2 ** 15, 0)
        self.r_motor = Motor(23, 24, 0, 2 ** 15, 0)
        # io_object, pin0, pin1, pwm0, pwm1, max_val, min_val
        self.v_motor = Motor_IBT2(5, 6, 1, 3, 2 ** 8, -1 * 2 ** 8)

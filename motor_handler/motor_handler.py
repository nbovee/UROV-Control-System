from motor import Motor, Motor_IBT2

l_motor = Motor(GPIO, 18, 17, 2, 2 ** 15, 0)
r_motor = Motor(GPIO, 23, 24, 0, 2 ** 15, 0)
        # v_motor is controlled by a different board type
        # io_object, pca, pin0, pin1, pwm0, pwm1, max_val, min_val
v_motor = Motor_IBT2(GPIO, 5, 6, 1, 3, 2 ** 8, -1 * 2 ** 8) #originally used GPIO27 & 22, moved due to suspected burnout

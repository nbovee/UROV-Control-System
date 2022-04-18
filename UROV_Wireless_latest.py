#!/usr/bin/python3

import sys
# import evdev for gamepad
from motor_handler.motor_handler import Motors
from controller_handler.controller import Controller
m = Motors()
c = Controller()

device = None

# event codes for the Logitech controller to be used
# comment out controls that will not be used.



def main():
    print("starting UROV_Wireless")

    global device
    try:
        # initialize Controller
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]

        for d in devices:
            if d.name == "Logitech Gamepad F710":
                # scan all devices and grab() the controller to avoid conflict
                device = evdev.InputDevice(d.path)
                device.grab()
                # output controller object for debug
                print(device.name + " connected.")
                break
        if device is None:
            print("Controller not found, check for power and correct Xinput/Dinput setting.")
            sys.exit()

        l_trig_last = 0
        r_trig_last = 0

        for event in device.read_loop():
            if event.type == evdev.ecodes.EV_KEY or evdev.ecodes.EV_ABS:
                if event.code in validCodes:
                    # print(f"{validCodes[event.code]}\t: {event.value}")
                    pulse = None
                    if validCodes[event.code] == 'L_Y':
                        # L motor control
                        pulse = m.l_motor.output(event.value)
                        print(pulse)

                    elif validCodes[event.code] == 'R_Y':
                        # R motor control
                        pulse = m.r_motor.output(event.value)
                        #print(pulse)

                    elif validCodes[event.code] == 'L_trig' or validCodes[event.code] == 'R_trig':
                        # V motor control, compares triggers against each other to avoid conflicts
                        if validCodes[event.code] == 'L_trig':
                            pulse = m.v_motor.output(event.value - r_trig_last)
                            l_trig_last = event.value
                            #print(pulse)
                        else:
                            pulse = m.v_motor.output(l_trig_last - event.value)
                            r_trig_last = event.value
                            #print(pulse)

                    elif validCodes[event.code] == 'L_bmp' and event.value == 1:
                        # invert left stick
                        m.l_motor.flip_stick()

                    elif validCodes[event.code] == 'R_bmp' and event.value == 1:
                        # invert right stick
                        m.r_motor.flip_stick()

                    elif validCodes[event.code] == 'Y' and event.value == 1:
                        # invert triggers
                        m.v_motor.flip_stick()

                    elif validCodes[event.code] == 'Start' and event.value == 1:
                        # reset axes and maybe recalibrate? figure out force feedback
                        # set all motors to no inversion
                        m.l_motor.reset_flip()
                        m.r_motor.reset_flip()
                        m.v_motor.reset_flip()
    except KeyboardInterrupt:
        print("Program stopped by KeyboardInterrupt.")
    except Exception as exception:
        #Output unexpected Exceptions.
        print(exception, False)
        print(exception.__class__.__name__ + ": " + exception.message)
        print("Progam Stopped unexpectedly. Please diagnose.")
    finally:
        if device is not None:
            device.ungrab()
        print("Controller freed.")

main()

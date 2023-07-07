#!/usr/bin/python3

# interpret evdev separate from the main controller
# potential multiplatform package "pip install hid"
import sys
import evdev # Linux only

device = None

# event codes for the Logitech controller to be used, comment out controls that will not be used.
validCodes = {
    # 0 : 'L_X',
    1: 'L_Y',
    2: 'L_trig',
    # 3 : 'R_X',
    4: 'R_Y',
    5: 'R_trig',
    # 16 : 'D_LR',
    # 17 : 'D_UD',
    # 304 : 'A',
    # 305 : 'B',
    # 307 : 'X',
    308: 'Y',
    310: 'L_bmp',
    311: 'R_bmp',
    # 314 : 'Select',
    315: 'Start',
    # 317 : 'L_3',
    # 318 : 'R_3'
}


def main():
    print(f"starting {__name__}")
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
                    print(f"{validCodes[event.code]}\t: {event.value}")
                    pulse = None
                    if validCodes[event.code] == 'L_Y':
                        # L motor control
                        pulse = l_motor.output(event.value)
                        print(pulse)

                    elif validCodes[event.code] == 'R_Y':
                        # R motor control
                        pulse = r_motor.output(event.value)
                        #print(pulse)

                    elif validCodes[event.code] == 'L_trig' or validCodes[event.code] == 'R_trig':
                        # V motor control, compares triggers against each other to avoid conflicts
                        if validCodes[event.code] == 'L_trig':
                            pulse = v_motor.output(event.value - r_trig_last)
                            l_trig_last = event.value
                            #print(pulse)
                        else:
                            pulse = v_motor.output(l_trig_last - event.value)
                            r_trig_last = event.value
                            #print(pulse)

                    elif validCodes[event.code] == 'L_bmp':
                        # invert left stick
                        if event.value == 1:
                            l_motor.flip_stick()

                    elif validCodes[event.code] == 'R_bmp':
                        # invert right stick
                        if event.value == 1:
                            r_motor.flip_stick()

                    elif validCodes[event.code] == 'Y':
                        # invert triggers
                        if event.value == 1:
                            v_motor.flip_stick()

                    elif validCodes[event.code] == 'Start':
                        # reset axes and maybe recalibrate? figure out force feedback
                        if event.value == 1:
                            # set all motors to no inversion
                            l_motor.reset_flip()
                            r_motor.reset_flip()
                            v_motor.reset_flip()
    except KeyboardInterrupt:
        print("Program stopped by KeyboardInterrupt.")
    except ImportError:
        print("Import Error")
    except Exception as exception:
        #Output unexpected Exceptions.
        print(exception, False)
        print(exception.__class__.__name__ + ": " + exception.message)
        print("Progam Stopped unexpectedly. Please diagnose.")
    finally:
        GPIO.cleanup()
        print("GPIO pins cleared.")
        if device is not None:
            device.ungrab()
        print("Controller freed.")

main()

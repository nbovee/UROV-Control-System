#!/usr/bin/python3

import sys
from motor import MotorSystem
from controller import Controller


def main():
    print("starting UROV_Wireless")

    controller = Controller()
    motors = None

    try:
        if not controller.connect():
            sys.exit()

        motors = MotorSystem()

        for event in controller.read_loop():
            if not Controller.is_valid_event(event):
                continue

            label = Controller.get_label(event)
            print(f"{label}\t: {event.value}")

            if label in motors.handlers:
                motors.handlers[label](event.value, label)

    except KeyboardInterrupt:
        print("Program stopped by KeyboardInterrupt.")
    except ImportError:
        print("Import Error")
    except Exception as exception:
        print(exception, False)
        print(exception.__class__.__name__ + ": " + str(exception))
        print("Program Stopped unexpectedly. Please diagnose.")
    finally:
        if motors is not None:
            motors.cleanup()
            print("GPIO pins cleared.")
        controller.disconnect()


main()

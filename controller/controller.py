import evdev


DEVICE_NAME = "Logitech Gamepad F710"

valid_codes = {
    1: "L_Y",
    2: "L_trig",
    4: "R_Y",
    5: "R_trig",
    308: "Y",
    310: "L_bmp",
    311: "R_bmp",
    315: "Start",
}


class Controller:
    def __init__(self):
        self.device = None

    def connect(self):
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for d in devices:
            if d.name == DEVICE_NAME:
                self.device = evdev.InputDevice(d.path)
                self.device.grab()
                print(self.device.name + " connected.")
                return True
        print(
            "Controller not found, check for power and correct Xinput/Dinput setting."
        )
        return False

    def read_loop(self):
        return self.device.read_loop()

    def disconnect(self):
        if self.device is not None:
            self.device.ungrab()
            print("Controller freed.")

    @staticmethod
    def is_valid_event(event):
        return (
            event.type == evdev.ecodes.EV_KEY or event.type == evdev.ecodes.EV_ABS
        ) and event.code in valid_codes

    @staticmethod
    def get_label(event):
        return valid_codes.get(event.code)

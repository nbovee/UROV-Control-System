import evdev

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

class Controller:
    def __init__(self):
        pass
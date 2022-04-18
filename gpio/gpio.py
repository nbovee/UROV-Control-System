import RPi.GPIO as _GPIO


class _SingleGPIO:
    instance = None

    def __init__(self):
        instance = _GPIO
        instance.setmode(_GPIO.BCM)


def single_gpio():
    if _SingleGPIO.instance is None:
        _SingleGPIO.instance = _SingleGPIO()
    return _SingleGPIO.instance

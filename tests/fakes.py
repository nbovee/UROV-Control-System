"""In-memory hardware doubles for I2C, PCA9685, and evdev tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class FakeSMBus:
    """Stand-in for ``smbus2.SMBus`` with register state and error injection."""

    def __init__(self, bus_num: int = 1) -> None:
        self.bus_num = bus_num
        self.registers: dict[int, int] = {}
        self.writes: list[tuple[int, int, int]] = []
        self.block_writes: list[tuple[int, int, list[int]]] = []
        self.closed = False
        self.close_calls = 0
        self.fail_reads: set[int] = set()
        self.fail_writes: set[int] = set()
        self.fail_blocks: set[int] = set()
        self.fail_all_reads = False
        self.fail_all_writes = False
        self.fail_all_blocks = False
        self.fail_close = False

    def read_byte_data(self, address: int, register: int) -> int:
        if self.fail_all_reads or register in self.fail_reads:
            raise OSError(f"injected read failure at register 0x{register:02X}")
        return self.registers.get(register, 0)

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        if self.fail_all_writes or register in self.fail_writes:
            raise OSError(f"injected write failure at register 0x{register:02X}")
        self.writes.append((address, register, value))
        self.registers[register] = value

    def write_i2c_block_data(self, address: int, register: int, data: list[int]) -> None:
        if self.fail_all_blocks or register in self.fail_blocks:
            raise OSError(f"injected block write failure at register 0x{register:02X}")
        payload = list(data)
        self.block_writes.append((address, register, payload))
        for offset, byte in enumerate(payload):
            self.registers[register + offset] = byte

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError("injected close failure")
        self.closed = True


class FakePCA9685:
    """Stand-in for ``PCA9685`` that records channel duties and call order."""

    def __init__(self) -> None:
        self.duties: dict[int, float] = {channel: 0.0 for channel in range(16)}
        self.calls: list[tuple] = []
        self.closed = False
        self.close_calls = 0
        self.all_off_calls = 0
        self.fail_channels: set[int] = set()
        self.fail_all_off = False
        self.fail_close = False

    def set_duty(self, channel: int, duty: float) -> None:
        if channel in self.fail_channels:
            raise OSError(f"injected PCA9685 failure on channel {channel}")
        self.calls.append(("set_duty", channel, duty))
        self.duties[channel] = duty

    def all_off(self) -> None:
        if self.fail_all_off:
            raise OSError("injected PCA9685 all_off failure")
        self.calls.append(("all_off",))
        self.all_off_calls += 1
        for channel in self.duties:
            self.duties[channel] = 0.0

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError("injected PCA9685 close failure")
        self.calls.append(("close",))
        self.closed = True


@dataclass
class FakeEvent:
    """Minimal ``evdev.InputEvent`` replacement."""

    type: int
    code: int
    value: int


@dataclass
class FakeAbsInfo:
    """Minimal ``evdev.AbsInfo`` replacement."""

    min: int
    max: int


class FakeInputDevice:
    """Minimal ``evdev.InputDevice`` replacement."""

    def __init__(
        self,
        path: str,
        name: str,
        events: list[FakeEvent] | None = None,
        absinfo: dict[int, FakeAbsInfo] | None = None,
        grab_error: OSError | None = None,
        read_error: OSError | None = None,
        batches: list[list[FakeEvent]] | None = None,
        hold_open: bool = False,
    ) -> None:
        self.path = path
        self.name = name
        self._events = list(events or [])
        self._batches = [list(batch) for batch in (batches or [])]
        self.events = self._events
        self.absinfo_map = dict(absinfo or {})
        self.grab_error = grab_error
        self.read_error = read_error
        self.hold_open = hold_open
        self.grabbed = False
        self.ungrabbed = False
        self.closed = False
        self.read_calls = 0
        self.stream_calls = 0
        self.stream_closed = False

    def grab(self) -> None:
        if self.grab_error is not None:
            raise self.grab_error
        self.grabbed = True

    def ungrab(self) -> None:
        self.ungrabbed = True

    def close(self) -> None:
        self.closed = True

    def _queued(self):
        yield from self._events
        for batch in self._batches:
            yield from batch

    def read(self):
        # A generator on purpose: callers must drain the whole batch.
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        yield from self._queued()

    async def async_read_loop(self):
        """Async event stream mirroring ``evdev.InputDevice.async_read_loop``."""
        self.stream_calls += 1
        try:
            if self.read_error is not None:
                raise self.read_error
            for event in self._queued():
                yield event
            if self.hold_open:
                # Simulate an idle-but-open device until the reader is closed.
                await asyncio.Event().wait()
        finally:
            self.stream_closed = True

    def absinfo(self, code: int) -> FakeAbsInfo | None:
        return self.absinfo_map.get(code)


class FakeEvdev:
    """Minimal ``evdev`` module replacement with open-failure injection."""

    def __init__(self, devices: list[FakeInputDevice]) -> None:
        self.devices = list(devices)
        self.input_device_calls: list[str] = []
        self.open_errors: set[str] = set()

    def list_devices(self) -> list[str]:
        return [device.path for device in self.devices]

    def InputDevice(self, path: str) -> FakeInputDevice:
        self.input_device_calls.append(path)
        if path in self.open_errors:
            raise OSError(f"cannot open {path}")
        for device in self.devices:
            if device.path == path:
                return device
        raise OSError(f"unknown device {path}")

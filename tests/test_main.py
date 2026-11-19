"""Deterministic async tests for the main control loop using fake time and hardware."""

import asyncio
import logging
import signal
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_module
from config import Config, load_config
from controller import EV_ABS, EV_KEY, ControllerDisconnected, InputMapper
from controller.controller import apply_deadzone, normalize_axis
from main import run_loop

DEFAULT = Path(__file__).parents[1] / "configs" / "default.toml"
BASE = load_config(DEFAULT)
START = 1000.0
PERIOD = 1.0 / BASE.control_rate_hz
EPS = 1e-9


class FakeClock:
    """A monotonic clock that only advances when told to."""

    def __init__(self, start: float = START) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delay: float) -> None:
        self.now += max(0.0, delay)


@dataclass
class Scheduled:
    when: float
    events: list | None = None
    disconnect: bool = False


class FakeController:
    """Scriptable async evdev controller with scheduled events and hotplug support."""

    def __init__(self, clock: FakeClock, config: Config, connect_failures: int = 0) -> None:
        self.clock = clock
        self.connected = False
        self.connect_failures = connect_failures
        self.connect_calls: list[float] = []
        self.disconnect_calls = 0
        self.events_started = 0
        self.events_closed = 0
        self._schedule: list[Scheduled] = []
        self._absinfo: dict[int, SimpleNamespace] = {}
        trigger_labels = {
            label for pair in config.controller.trigger_mux.values() for label in pair
        }
        for code, label in config.controller.axes.items():
            if label in trigger_labels:
                self._absinfo[code] = SimpleNamespace(min=0, max=255)
            else:
                self._absinfo[code] = SimpleNamespace(min=-32768, max=32767)

    def at(self, when: float, *events: SimpleNamespace) -> None:
        self._schedule.append(Scheduled(when=when, events=list(events)))
        self._schedule.sort(key=lambda item: item.when)

    def disconnect_at(self, when: float) -> None:
        self._schedule.append(Scheduled(when=when, disconnect=True))
        self._schedule.sort(key=lambda item: item.when)

    def connect(self) -> bool:
        self.connect_calls.append(self.clock())
        if self.connect_failures > 0:
            self.connect_failures -= 1
            return False
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    def absinfo(self, code: int) -> SimpleNamespace | None:
        return self._absinfo.get(code)

    async def events(self):
        """Yield scheduled events as the fake clock reaches them."""
        self.events_started += 1
        try:
            while self.connected:
                if self._schedule and self._schedule[0].when <= self.clock():
                    item = self._schedule.pop(0)
                    self.clock.advance(item.when - self.clock())
                    if item.disconnect:
                        self.connected = False
                        raise ControllerDisconnected("fake controller disconnected")
                    for event in item.events or []:
                        yield event
                    continue
                await asyncio.sleep(0)
        finally:
            self.events_closed += 1


class FakeMotors:
    """Records motor updates and can fail selected I2C writes."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.applied: list[tuple[float, dict[str, float]]] = []
        self.stop_calls: list[float] = []
        self.cleanup_calls = 0
        self.apply_calls = 0
        self.fail_apply_calls: set[int] = set()

    def apply(self, setpoints: dict[str, float]) -> None:
        call = self.apply_calls
        self.apply_calls += 1
        if call in self.fail_apply_calls:
            raise OSError("fake I2C bus error")
        self.applied.append((self.clock(), dict(setpoints)))

    def stop_all(self) -> None:
        self.stop_calls.append(self.clock())

    def cleanup(self) -> None:
        self.cleanup_calls += 1


def button(config: Config, label: str) -> int:
    return next(code for code, name in config.controller.buttons.items() if name == label)


def axis(config: Config, label: str) -> int:
    return next(code for code, name in config.controller.axes.items() if name == label)


def key(config: Config, label: str, value: int = 1) -> SimpleNamespace:
    return SimpleNamespace(type=EV_KEY, code=button(config, label), value=value)


def move(config: Config, label: str, value: int) -> SimpleNamespace:
    return SimpleNamespace(type=EV_ABS, code=axis(config, label), value=value)


def rig(config: Config = BASE, **controller_kwargs):
    clock = FakeClock()
    controller = FakeController(clock, config, **controller_kwargs)
    motors = FakeMotors(clock)
    mapper = InputMapper(config)
    return clock, controller, motors, mapper


def fake_sleep(clock: FakeClock):
    """Advance fake time, then yield so the input task can run."""

    async def sleep(delay: float) -> None:
        clock.advance(delay)
        await asyncio.sleep(0)

    return sleep


def throttle(config: Config, label: str, raw: int) -> float:
    """Expected mapped throttle for a raw axis value on a deadzoned motor."""
    value = normalize_axis(raw, -32768, 32767)
    return apply_deadzone(value, config.controller.deadzone)


def is_zero(setpoints: dict[str, float]) -> bool:
    return all(abs(value) < EPS for value in setpoints.values())


def test_boots_without_gamepad_then_hotplugs_disarmed() -> None:
    clock, controller, motors, mapper = rig(connect_failures=1)
    arm_at = START + 0.6
    drive_at = START + 0.62
    controller.at(arm_at, key(BASE, "A"))
    controller.at(drive_at, move(BASE, "L_Y", 32767))

    code = asyncio.run(
        run_loop(
            BASE,
            controller,
            motors,
            mapper,
            clock=clock,
            sleep=fake_sleep(clock),
            max_ticks=40,
        )
    )

    assert code == 0
    # Outputs are stopped while the gamepad is missing.
    assert motors.stop_calls
    # The loop retries at the configured reconnect interval and then connects.
    assert len(controller.connect_calls) == 2
    assert controller.connect_calls[0] == pytest.approx(START)
    assert controller.connect_calls[1] == pytest.approx(
        START + BASE.controller.reconnect_interval_s, abs=PERIOD + EPS
    )
    # Reconnect starts disarmed: no non-zero output before the A press.
    driven = [(when, setpoints) for when, setpoints in motors.applied if not is_zero(setpoints)]
    assert driven
    assert all(when >= arm_at - EPS for when, _ in driven)
    assert all(is_zero(setpoints) for when, setpoints in motors.applied if when < arm_at - EPS)
    assert mapper.armed is True
    assert motors.applied[-1][1]["left"] == pytest.approx(1.0)


def test_async_events_use_latest_value_before_tick_apply() -> None:
    clock, controller, motors, mapper = rig()
    controller.at(START, key(BASE, "A"), move(BASE, "L_Y", 32767))
    controller.at(START, move(BASE, "L_Y", 1000))

    code = asyncio.run(
        run_loop(
            BASE,
            controller,
            motors,
            mapper,
            clock=clock,
            sleep=fake_sleep(clock),
            max_ticks=1,
        )
    )

    assert code == 0
    # Both batches were consumed on the same tick and the newer value won.
    assert mapper.controls["L_Y"] == pytest.approx(1000 / 32767)
    assert motors.applied[-1][1]["left"] == pytest.approx(throttle(BASE, "L_Y", 1000))
    assert 0.0 < motors.applied[-1][1]["left"] < 0.05


def test_estop_stops_motors_while_sticks_are_held() -> None:
    clock, controller, motors, mapper = rig()
    estop_at = START + 0.04
    held_at = START + 0.06
    controller.at(START, key(BASE, "A"))
    controller.at(START + 0.02, move(BASE, "L_Y", 32767))
    controller.at(estop_at, key(BASE, "B"))
    controller.at(held_at, move(BASE, "L_Y", 32767))

    code = asyncio.run(
        run_loop(
            BASE,
            controller,
            motors,
            mapper,
            clock=clock,
            sleep=fake_sleep(clock),
            max_ticks=10,
        )
    )

    assert code == 0
    assert mapper.armed is False
    assert all(abs(value) < EPS for value in mapper.motor_setpoints().values())
    assert any(when >= estop_at - EPS for when in motors.stop_calls)
    # No motor is applied after the E-stop; the held stick cannot drive while disarmed.
    assert all(when < estop_at + EPS for when, _ in motors.applied)
    assert motors.stop_calls[-1] >= held_at - EPS


def test_controller_disconnect_stops_disarms_and_reconnects() -> None:
    clock, controller, motors, mapper = rig()
    disconnect_at = START + 0.05
    reconnect_at = START + 0.56
    full_after_reconnect = START + 0.57
    center_at = START + 0.59
    rearm_at = START + 0.61
    drive_at = START + 0.63

    controller.at(
        START,
        key(BASE, "A"),
        move(BASE, "L_trig", 255),
        move(BASE, "L_Y", 32767),
    )
    controller.disconnect_at(disconnect_at)
    controller.at(full_after_reconnect, move(BASE, "L_Y", 32767))
    controller.at(center_at, move(BASE, "L_Y", 0))
    controller.at(rearm_at, key(BASE, "A"))
    controller.at(drive_at, move(BASE, "L_Y", 32767))

    code = asyncio.run(
        run_loop(
            BASE,
            controller,
            motors,
            mapper,
            clock=clock,
            sleep=fake_sleep(clock),
            max_ticks=40,
        )
    )

    assert code == 0
    assert controller.disconnect_calls == 1
    assert len(controller.connect_calls) == 2
    assert controller.connect_calls[1] == pytest.approx(reconnect_at, abs=PERIOD + EPS)
    # Disconnect immediately stops all motors and disarms.
    assert any(abs(when - disconnect_at) < PERIOD + EPS for when in motors.stop_calls)
    # A held stick right after reconnect is ignored until the operator re-arms.
    for when, setpoints in motors.applied:
        if disconnect_at <= when < drive_at:
            assert is_zero(setpoints)
    driven = [(when, setpoints) for when, setpoints in motors.applied if not is_zero(setpoints)]
    assert driven
    assert all(when < disconnect_at or when >= drive_at - EPS for when, _ in driven)
    # Trigger state was reset on disconnect and the mapper re-armed cleanly.
    assert mapper.controls["vertical"] == pytest.approx(0.0)
    assert mapper.armed is True
    assert motors.applied[-1][1]["left"] == pytest.approx(1.0)


def test_watchdog_disabled_allows_steady_hold() -> None:
    assert BASE.watchdog_timeout_s == 0.0
    clock, controller, motors, mapper = rig()
    controller.at(
        START,
        key(BASE, "A"),
        move(BASE, "L_trig", 255),
        move(BASE, "L_Y", 32767),
    )

    code = asyncio.run(
        run_loop(
            BASE,
            controller,
            motors,
            mapper,
            clock=clock,
            sleep=fake_sleep(clock),
            max_ticks=50,
        )
    )

    assert code == 0
    assert mapper.armed is True
    assert motors.stop_calls == []
    assert motors.applied[-1][1]["left"] == pytest.approx(1.0)
    assert motors.applied[-1][1]["vertical"] == pytest.approx(1.0)


def test_watchdog_timeout_zeroes_controls_and_triggers(caplog) -> None:
    config = replace(BASE, watchdog_timeout_s=0.5)
    clock, controller, motors, mapper = rig(config)
    controller.at(
        START,
        key(config, "A"),
        move(config, "L_trig", 255),
        move(config, "L_Y", 32767),
    )

    with caplog.at_level(logging.WARNING):
        code = asyncio.run(
            run_loop(
                config,
                controller,
                motors,
                mapper,
                clock=clock,
                sleep=fake_sleep(clock),
                max_ticks=40,
            )
        )

    assert code == 0
    assert mapper.armed is True
    assert mapper.controls["L_Y"] == pytest.approx(0.0)
    assert mapper.controls["vertical"] == pytest.approx(0.0)
    assert is_zero(motors.applied[-1][1])
    assert "Input watchdog timeout" in caplog.text
    assert caplog.text.count("Input watchdog timeout") == 1


def test_stop_event_exits_loop_cleanly() -> None:
    clock, controller, motors, _ = rig()
    stop_event = asyncio.Event()
    sleeps: list[float] = []

    async def fake_stop_sleep(delay: float) -> None:
        if delay > 0.0:
            sleeps.append(delay)
        clock.advance(delay)
        await asyncio.sleep(0)
        if len(sleeps) >= 3:
            stop_event.set()

    code = asyncio.run(
        run_loop(
            BASE,
            controller,
            motors,
            clock=clock,
            sleep=fake_stop_sleep,
            stop_event=stop_event,
        )
    )

    assert code == 0
    assert stop_event.is_set()
    assert len(sleeps) == 3
    # The background input task was cancelled and its event stream closed.
    assert controller.events_closed == 1


def test_transient_i2c_error_recovers(caplog) -> None:
    clock, controller, motors, _ = rig()
    controller.at(START, key(BASE, "A"), move(BASE, "L_Y", 32767))
    motors.fail_apply_calls = {0}

    with caplog.at_level(logging.ERROR):
        code = asyncio.run(
            run_loop(
                BASE,
                controller,
                motors,
                clock=clock,
                sleep=fake_sleep(clock),
                max_ticks=3,
            )
        )

    assert code == 0
    assert motors.apply_calls == 3
    assert len(motors.applied) == 2
    assert all(setpoints["left"] == pytest.approx(1.0) for _, setpoints in motors.applied)
    assert len(motors.stop_calls) == 1
    assert "I2C error during motor update (1/10)" in caplog.text


def test_consecutive_i2c_counter_resets_after_success() -> None:
    clock, controller, motors, _ = rig()
    controller.at(START, key(BASE, "A"), move(BASE, "L_Y", 32767))
    motors.fail_apply_calls = {0, 1, 3, 4}

    code = asyncio.run(
        run_loop(
            BASE,
            controller,
            motors,
            clock=clock,
            sleep=fake_sleep(clock),
            max_ticks=6,
            max_i2c_errors=3,
        )
    )

    assert code == 0
    assert motors.apply_calls == 6
    assert len(motors.applied) == 2
    assert len(motors.stop_calls) == 4


def test_persistent_i2c_errors_exit_with_failure(caplog) -> None:
    clock, controller, motors, _ = rig()
    controller.at(START, key(BASE, "A"), move(BASE, "L_Y", 32767))
    motors.fail_apply_calls = set(range(100))

    with caplog.at_level(logging.ERROR):
        code = asyncio.run(
            run_loop(
                BASE,
                controller,
                motors,
                clock=clock,
                sleep=fake_sleep(clock),
                max_ticks=100,
                max_i2c_errors=3,
            )
        )

    assert code == 1
    assert motors.apply_calls == 3
    assert motors.applied == []
    assert len(motors.stop_calls) == 3
    assert "Exceeded maximum consecutive I2C errors" in caplog.text


def test_main_returns_2_on_configuration_error(monkeypatch, caplog) -> None:
    def fail(_path):
        raise ValueError("bad configuration")

    monkeypatch.setattr(main_module, "load_config", fail)

    with caplog.at_level(logging.ERROR):
        assert main_module.main("missing.toml") == 2

    assert "Failed to load configuration" in caplog.text


def test_main_cleans_up_and_restores_signal_handlers(monkeypatch) -> None:
    created: dict[str, object] = {}

    class FakeMainMotors:
        def __init__(self, config) -> None:
            self.cleaned = False
            created["motors"] = self

        def cleanup(self) -> None:
            self.cleaned = True

    class FakeMainController:
        def __init__(self, config) -> None:
            self.disconnected = False
            created["controller"] = self

        def disconnect(self) -> None:
            self.disconnected = True

    async def fake_run_loop(*args, **kwargs) -> int:
        return 0

    monkeypatch.setattr(main_module, "load_config", lambda _path: BASE)
    monkeypatch.setattr(main_module, "MotorSystem", FakeMainMotors)
    monkeypatch.setattr(main_module, "Controller", FakeMainController)
    monkeypatch.setattr(main_module, "run_loop", fake_run_loop)

    installable = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGHUP"):
        installable.append(signal.SIGHUP)
    before = {signum: signal.getsignal(signum) for signum in installable}

    assert main_module.main(None) == 0
    assert created["motors"].cleaned is True
    assert created["controller"].disconnected is True
    for signum in installable:
        assert signal.getsignal(signum) is before[signum]


def test_main_cleans_up_when_loop_raises(monkeypatch, caplog) -> None:
    created: dict[str, object] = {}

    class FakeMainMotors:
        def __init__(self, config) -> None:
            self.cleaned = False
            created["motors"] = self

        def cleanup(self) -> None:
            self.cleaned = True

    class FakeMainController:
        def __init__(self, config) -> None:
            self.disconnected = False
            created["controller"] = self

        def disconnect(self) -> None:
            self.disconnected = True

    async def failing_run_loop(*args, **kwargs) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "load_config", lambda _path: BASE)
    monkeypatch.setattr(main_module, "MotorSystem", FakeMainMotors)
    monkeypatch.setattr(main_module, "Controller", FakeMainController)
    monkeypatch.setattr(main_module, "run_loop", failing_run_loop)

    with caplog.at_level(logging.ERROR):
        assert main_module.main(None) == 1

    assert "Unhandled error in control loop" in caplog.text
    assert created["motors"].cleaned is True
    assert created["controller"].disconnected is True

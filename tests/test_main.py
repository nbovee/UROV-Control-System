import asyncio
import logging
import signal
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_module
from config import load_config
from controller import EV_ABS, EV_KEY, ControllerDisconnected, InputMapper
from controller.controller import apply_deadzone, normalize_axis
from main import run_loop

BASE = load_config(Path(__file__).parents[1] / "configs" / "default.toml")
START, EPS = 1000.0, 1e-9
PERIOD = 1.0 / BASE.control_rate_hz
LATEST_LEFT = apply_deadzone(normalize_axis(1000, -32768, 32767), BASE.controller.deadzone)


class FakeClock:
    now = START
    def __call__(self): return self.now
    def advance(self, delay): self.now += max(0.0, delay)


class FakeController:
    def __init__(self, clock, _config, connect_failures=0):
        self.clock, self.failures = clock, connect_failures
        self.connected, self.schedule = False, []
        self.connect_calls, self.disconnect_calls, self.events_closed = [], 0, 0
    def connect(self):
        self.connect_calls.append(self.clock())
        self.connected = self.failures == 0
        self.failures = max(0, self.failures - 1)
        return self.connected
    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False
    def absinfo(self, _code): return None
    async def events(self):
        try:
            while self.connected:
                if self.schedule and self.schedule[0][0] <= self.clock():
                    events = self.schedule.pop(0)[1]
                    if not events:
                        self.connected = False
                        raise ControllerDisconnected("fake controller disconnected")
                    for event in events:
                        yield event
                    continue
                await asyncio.sleep(0)
        finally:
            self.events_closed += 1


class FakeMotors:
    def __init__(self, clock):
        self.clock, self.applied, self.stop_calls = clock, [], []
        self.apply_calls, self.fail_apply_calls = 0, set()
    def apply(self, setpoints):
        call, self.apply_calls = self.apply_calls, self.apply_calls + 1
        if call in self.fail_apply_calls:
            raise OSError("fake I2C bus error")
        self.applied.append((self.clock(), dict(setpoints)))
    def stop_all(self): self.stop_calls.append(self.clock())


BUTTONS = {name: code for code, name in BASE.controller.buttons.items()}
AXES = {name: code for code, name in BASE.controller.axes.items()}
def K(label, value=1): return SimpleNamespace(type=EV_KEY, code=BUTTONS[label], value=value)
def M(label, value): return SimpleNamespace(type=EV_ABS, code=AXES[label], value=value)


ARM_DRIVE = (K("A"), M("L_Y", 32767))
ARM_TRIGGER_DRIVE = (K("A"), M("L_trig", 255), M("L_Y", 32767))


def scenario(events=(), *, config=BASE, max_ticks=None, connect_failures=0,
             fail_apply_calls=(), sleep=None, **kwargs):
    clock = FakeClock()

    async def tick(delay):
        clock.advance(delay)
        await asyncio.sleep(0)

    controller = FakeController(clock, config, connect_failures)
    motors = FakeMotors(clock)
    motors.fail_apply_calls = set(fail_apply_calls)
    mapper = InputMapper(config)
    controller.schedule = sorted(events, key=lambda item: item[0])
    code = asyncio.run(run_loop(config, controller, motors, mapper, clock=clock,
                                sleep=sleep(clock) if sleep else tick,
                                max_ticks=max_ticks, **kwargs))
    return controller, motors, mapper, code


def is_zero(setpoints): return all(abs(value) < EPS for value in setpoints.values())
def driven(motors): return [(when, sp) for when, sp in motors.applied if not is_zero(sp)]
def assert_connects(controller, *times):
    assert controller.connect_calls == pytest.approx(times, abs=PERIOD + EPS)


def check_boot_hotplug(controller, motors, mapper):
    assert mapper.armed
    assert_connects(controller, START, START + BASE.controller.reconnect_interval_s)
    assert motors.stop_calls
    assert all(is_zero(sp) for when, sp in motors.applied if when < START + 0.6 - EPS)
    assert driven(motors)
    assert all(when >= START + 0.6 - EPS for when, _ in driven(motors))
    last = motors.applied[-1][1]
    assert last["left"] == pytest.approx(1.0)
    assert last["vertical_primary"] == last["vertical_secondary"] == pytest.approx(0.0)


def check_latest_wins(_controller, motors, mapper):
    assert mapper.controls["L_Y"] == pytest.approx(1000 / 32767)
    assert motors.applied[-1][1]["left"] == pytest.approx(LATEST_LEFT)


def check_estop(_controller, motors, mapper):
    assert not mapper.armed
    assert is_zero(mapper.motor_setpoints())
    assert any(abs(when - (START + 0.04)) < PERIOD + EPS for when in motors.stop_calls)
    assert all(when < START + 0.04 + EPS for when, _ in motors.applied)


def check_disconnect(controller, motors, mapper):
    assert mapper.armed
    assert_connects(controller, START, START + 0.56)
    assert controller.disconnect_calls == 1
    assert any(abs(when - (START + 0.05)) < PERIOD + EPS for when in motors.stop_calls)
    start, end = START + 0.05, START + 0.63
    assert all(is_zero(sp) for when, sp in motors.applied if start <= when < end)
    assert all(when < start or when >= end - EPS for when, _ in driven(motors))
    assert mapper.controls["vertical"] == pytest.approx(0.0)
    assert motors.applied[-1][1]["left"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("events", "options", "check"),
    [
        pytest.param(
            [(START + 0.6, (K("A"),)), (START + 0.62, (M("L_Y", 32767),))],
            {"connect_failures": 1, "max_ticks": 40},
            check_boot_hotplug, id="boot-hotplug-arm-drive",
        ),
        pytest.param(
            [(START, ARM_DRIVE), (START, (M("L_Y", 1000),))],
            {"max_ticks": 1}, check_latest_wins, id="latest-event-wins",
        ),
        pytest.param(
            [(START, (K("A"),)), (START + 0.02, (M("L_Y", 32767),)),
             (START + 0.04, (K("B"),)), (START + 0.06, (M("L_Y", 32767),))],
            {"max_ticks": 10}, check_estop, id="estop-stops-immediately",
        ),
        pytest.param(
            [(START, ARM_TRIGGER_DRIVE), (START + 0.05, ()),
             (START + 0.57, (M("L_Y", 32767),)), (START + 0.59, (M("L_Y", 0),)),
             (START + 0.61, (K("A"),)), (START + 0.63, (M("L_Y", 32767),))],
            {"max_ticks": 40}, check_disconnect, id="disconnect-stops-and-reconnects",
        ),
    ],
)
def test_run_loop_control_flow(events, options, check):
    controller, motors, mapper, code = scenario(events, **options)
    assert code == 0
    check(controller, motors, mapper)


def check_watchdog_disabled(_controller, motors, mapper, _caplog):
    assert mapper.armed and motors.stop_calls == []
    last = motors.applied[-1][1]
    assert last["left"] == pytest.approx(1.0)
    assert last["vertical_primary"] == last["vertical_secondary"] == pytest.approx(1.0)


def check_watchdog_enabled(_controller, motors, mapper, caplog):
    assert mapper.armed
    assert mapper.controls["L_Y"] == mapper.controls["vertical"] == pytest.approx(0.0)
    assert is_zero(motors.applied[-1][1])
    assert caplog.text.count("Input watchdog timeout") == 1


@pytest.mark.parametrize(
    ("config", "max_ticks", "check"),
    [
        pytest.param(BASE, 50, check_watchdog_disabled, id="disabled-holds-steady"),
        pytest.param(replace(BASE, watchdog_timeout_s=0.5), 40, check_watchdog_enabled,
                     id="enabled-zeros-after-timeout"),
    ],
)
def test_run_loop_watchdog(caplog, config, max_ticks, check):
    with caplog.at_level(logging.WARNING):
        controller, motors, mapper, code = scenario(
            [(START, ARM_TRIGGER_DRIVE)], config=config, max_ticks=max_ticks)
    assert code == 0
    check(controller, motors, mapper, caplog)


def check_i2c_transient(code, motors, caplog):
    assert code == 0
    assert motors.apply_calls == 4 and len(motors.applied) == 2
    assert len(motors.stop_calls) == 2
    assert all(sp["left"] == pytest.approx(1.0) for _, sp in motors.applied)
    assert caplog.text.count("I2C error during motor update (1/2)") == 2


def check_i2c_persistent(code, motors, caplog):
    assert code == 1
    assert motors.apply_calls == 3 and motors.applied == []
    assert len(motors.stop_calls) == 3
    assert "Exceeded maximum consecutive I2C errors" in caplog.text


@pytest.mark.parametrize(
    ("fail_apply_calls", "max_ticks", "max_i2c_errors", "check"),
    [
        pytest.param({0, 2}, 4, 2, check_i2c_transient, id="transient-recovers"),
        pytest.param(set(range(100)), 100, 3, check_i2c_persistent,
                     id="persistent-exits-nonzero"),
    ],
)
def test_run_loop_i2c_errors(caplog, fail_apply_calls, max_ticks, max_i2c_errors, check):
    with caplog.at_level(logging.WARNING):
        _, motors, _, code = scenario([(START, ARM_DRIVE)], max_ticks=max_ticks,
                                      fail_apply_calls=fail_apply_calls,
                                      max_i2c_errors=max_i2c_errors)
    check(code, motors, caplog)


def test_run_loop_stop_event():
    stop_event, sleeps = asyncio.Event(), []

    def sleeper(clock):
        async def sleep(delay):
            if delay > 0.0:
                sleeps.append(delay)
            clock.advance(delay)
            await asyncio.sleep(0)
            if len(sleeps) >= 3:
                stop_event.set()
        return sleep

    controller, _motors, _mapper, code = scenario(sleep=sleeper, stop_event=stop_event)
    assert code == 0
    assert len(sleeps) == 3 and stop_event.is_set()
    assert controller.events_closed == 1


def patch_main(monkeypatch, loop, *, load_error=False):
    created = []

    class Hardware:
        def __init__(self, _config):
            created.append(self)
            self.cleaned = self.disconnected = False
        def cleanup(self): self.cleaned = True
        def disconnect(self): self.disconnected = True

    def load(_path):
        if load_error:
            raise ValueError("bad configuration")
        return BASE

    for name, fake in (("load_config", load), ("MotorSystem", Hardware),
                       ("Controller", Hardware), ("run_loop", loop)):
        monkeypatch.setattr(main_module, name, fake)
    return created


@pytest.mark.parametrize(
    ("load_error", "loop_error", "code", "message"),
    [
        pytest.param(True, False, 2, "Failed to load configuration", id="config-error"),
        pytest.param(False, False, 0, None, id="clean-exit"),
        pytest.param(False, True, 1, "Unhandled error in control loop", id="unhandled-error"),
    ],
)
def test_main_lifecycle(monkeypatch, caplog, load_error, loop_error, code, message):
    async def loop(*_args, **_kwargs):
        if loop_error:
            raise RuntimeError("boom")
        return 0

    created = patch_main(monkeypatch, loop, load_error=load_error)
    signals = [s for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None))
               if s is not None]
    before = {s: signal.getsignal(s) for s in signals}

    with caplog.at_level(logging.ERROR):
        assert main_module.main(None) == code

    assert message is None or message in caplog.text
    assert not created or (created[0].cleaned and created[1].disconnected)
    assert bool(created) == (not load_error)
    assert all(signal.getsignal(s) is before[s] for s in signals)

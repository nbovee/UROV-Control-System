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


class FakeClock:
    now = START

    def __call__(self):
        return self.now

    def advance(self, delay):
        self.now += max(0.0, delay)


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

    def absinfo(self, code):
        return None

    async def events(self):
        try:
            while self.connected:
                if self.schedule and self.schedule[0][0] <= self.clock():
                    when, events = self.schedule.pop(0)
                    self.clock.advance(when - self.clock())
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
        self.clock = clock
        self.applied, self.stop_calls = [], []
        self.apply_calls, self.fail_apply_calls = 0, set()

    def apply(self, setpoints):
        call = self.apply_calls
        self.apply_calls += 1
        if call in self.fail_apply_calls:
            raise OSError("fake I2C bus error")
        self.applied.append((self.clock(), dict(setpoints)))

    def stop_all(self):
        self.stop_calls.append(self.clock())


def K(label, value=1):
    code = next(c for c, name in BASE.controller.buttons.items() if name == label)
    return SimpleNamespace(type=EV_KEY, code=code, value=value)


def M(label, value):
    code = next(c for c, name in BASE.controller.axes.items() if name == label)
    return SimpleNamespace(type=EV_ABS, code=code, value=value)


def scenario(events=(), *, config=BASE, max_ticks=None, connect_failures=0,
             fail_apply_calls=(), sleep=None, **kwargs):
    clock = FakeClock()

    async def tick(delay):
        clock.advance(delay)
        await asyncio.sleep(0)

    controller = FakeController(clock, config, connect_failures=connect_failures)
    motors = FakeMotors(clock)
    motors.fail_apply_calls = set(fail_apply_calls)
    mapper = InputMapper(config)
    controller.schedule = sorted(events, key=lambda item: item[0])
    code = asyncio.run(
        run_loop(config, controller, motors, mapper, clock=clock,
                 sleep=sleep(clock) if sleep else tick, max_ticks=max_ticks, **kwargs)
    )
    return controller, motors, mapper, code


def is_zero(setpoints):
    return all(abs(value) < EPS for value in setpoints.values())


def test_boot_hotplug_and_latest_event_wins():
    arm_at, drive_at = START + 0.6, START + 0.62
    controller, motors, mapper, code = scenario(
        [(arm_at, (K("A"),)), (drive_at, (M("L_Y", 32767),))],
        max_ticks=40, connect_failures=1)
    assert code == 0 and motors.stop_calls and len(controller.connect_calls) == 2
    assert controller.connect_calls[0] == pytest.approx(START)
    assert controller.connect_calls[1] == pytest.approx(
        START + BASE.controller.reconnect_interval_s, abs=PERIOD + EPS)
    driven = [(w, s) for w, s in motors.applied if not is_zero(s)]
    assert driven and all(w >= arm_at - EPS for w, _ in driven)
    assert all(is_zero(s) for w, s in motors.applied if w < arm_at - EPS)
    assert mapper.armed and motors.applied[-1][1]["left"] == pytest.approx(1.0)
    _, motors, mapper, code = scenario(
        [(START, (K("A"), M("L_Y", 32767))), (START, (M("L_Y", 1000),))], max_ticks=1)
    last = motors.applied[-1][1]["left"]
    expected = apply_deadzone(normalize_axis(1000, -32768, 32767), BASE.controller.deadzone)
    assert code == 0 and mapper.controls["L_Y"] == pytest.approx(1000 / 32767)
    assert last == pytest.approx(expected) and 0.0 < last < 0.05


def test_estop_stops_motors_while_sticks_are_held():
    estop_at, held_at = START + 0.04, START + 0.06
    _, motors, mapper, code = scenario([
        (START, (K("A"),)), (START + 0.02, (M("L_Y", 32767),)),
        (estop_at, (K("B"),)), (held_at, (M("L_Y", 32767),)),
    ], max_ticks=10)
    assert code == 0 and mapper.armed is False and is_zero(mapper.motor_setpoints())
    assert any(w >= estop_at - EPS for w in motors.stop_calls)
    assert all(w < estop_at + EPS for w, _ in motors.applied)
    assert motors.stop_calls[-1] >= held_at - EPS


def test_controller_disconnect_stops_disarms_and_reconnects():
    disconnect_at, drive_at = START + 0.05, START + 0.63
    controller, motors, mapper, code = scenario([
        (START, (K("A"), M("L_trig", 255), M("L_Y", 32767))),
        (disconnect_at, ()), (START + 0.57, (M("L_Y", 32767),)),
        (START + 0.59, (M("L_Y", 0),)), (START + 0.61, (K("A"),)),
        (drive_at, (M("L_Y", 32767),)),
    ], max_ticks=40)
    assert code == 0 and controller.disconnect_calls == 1 and len(controller.connect_calls) == 2
    assert controller.connect_calls[1] == pytest.approx(START + 0.56, abs=PERIOD + EPS)
    assert any(abs(w - disconnect_at) < PERIOD + EPS for w in motors.stop_calls)
    assert all(is_zero(s) for w, s in motors.applied if disconnect_at <= w < drive_at)
    driven = [(w, s) for w, s in motors.applied if not is_zero(s)]
    assert driven and all(w < disconnect_at or w >= drive_at - EPS for w, _ in driven)
    assert mapper.controls["vertical"] == pytest.approx(0.0)
    assert mapper.armed and motors.applied[-1][1]["left"] == pytest.approx(1.0)


def test_watchdog_holds_steady_or_times_out(caplog):
    events = [(START, (K("A"), M("L_trig", 255), M("L_Y", 32767)))]
    assert BASE.watchdog_timeout_s == 0.0
    _, motors, mapper, code = scenario(events, max_ticks=50)
    last = motors.applied[-1][1]
    assert code == 0 and mapper.armed is True and motors.stop_calls == []
    assert last["left"] == pytest.approx(1.0) and last["vertical"] == pytest.approx(1.0)
    with caplog.at_level(logging.WARNING):
        _, motors, mapper, code = scenario(
            events, config=replace(BASE, watchdog_timeout_s=0.5), max_ticks=40)
    assert code == 0 and mapper.armed is True
    assert mapper.controls["L_Y"] == mapper.controls["vertical"] == pytest.approx(0.0)
    assert is_zero(motors.applied[-1][1])
    assert caplog.text.count("Input watchdog timeout") == 1


def test_stop_event_exits_loop_cleanly():
    stop_event, sleeps = asyncio.Event(), []

    def stop_sleep(clock):
        async def sleep(delay):
            if delay > 0.0:
                sleeps.append(delay)
            clock.advance(delay)
            await asyncio.sleep(0)
            if len(sleeps) >= 3:
                stop_event.set()
        return sleep
    controller, _, _, code = scenario(sleep=stop_sleep, stop_event=stop_event)
    assert code == 0 and stop_event.is_set() and len(sleeps) == 3
    assert controller.events_closed == 1


@pytest.mark.parametrize(("t", "e", "f", "c", "a", "s", "msg"),
    [(3, 10, {0}, 3, 2, 1, "I2C error during motor update (1/10)"),
     (6, 3, {0, 1, 3, 4}, 6, 2, 4, None),
     (100, 3, range(100), 3, 0, 3, "Exceeded maximum consecutive I2C errors")])
def test_i2c_error_handling(caplog, t, e, f, c, a, s, msg):
    with caplog.at_level(logging.ERROR):
        _, motors, _, code = scenario(
            [(START, (K("A"), M("L_Y", 32767)))], max_ticks=t,
            max_i2c_errors=e, fail_apply_calls=f)
    assert code == (0 if a else 1) and motors.apply_calls == c
    assert len(motors.applied) == a and len(motors.stop_calls) == s
    assert a == 0 or all(sp["left"] == pytest.approx(1.0) for _, sp in motors.applied)
    assert msg is None or msg in caplog.text


def test_main_returns_2_on_configuration_error(monkeypatch, caplog):
    def fail(_path):
        raise ValueError("bad configuration")
    monkeypatch.setattr(main_module, "load_config", fail)
    with caplog.at_level(logging.ERROR):
        assert main_module.main("missing.toml") == 2
    assert "Failed to load configuration" in caplog.text


def patch_main(monkeypatch, loop_impl):
    created = []

    class Hardware:
        def __init__(self, _config):
            created.append(self)
            self.cleaned = self.disconnected = False

        def cleanup(self):
            self.cleaned = True

        def disconnect(self):
            self.disconnected = True

    for name, fake in (("load_config", lambda _path: BASE), ("MotorSystem", Hardware),
                       ("Controller", Hardware), ("run_loop", loop_impl)):
        monkeypatch.setattr(main_module, name, fake)
    return created


@pytest.mark.parametrize("fail", [False, True])
def test_main_cleans_up_and_restores_handlers(monkeypatch, caplog, fail):
    async def loop(*_args, **_kwargs):
        if fail:
            raise RuntimeError("boom")
        return 0
    created = patch_main(monkeypatch, loop)
    signals = [s for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None))
               if s is not None]
    before = {s: signal.getsignal(s) for s in signals}
    with caplog.at_level(logging.ERROR):
        assert main_module.main(None) == int(fail)
    assert not fail or "Unhandled error in control loop" in caplog.text
    assert created[0].cleaned and created[1].disconnected
    assert all(signal.getsignal(s) is before[s] for s in signals)

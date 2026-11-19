#!/usr/bin/env python3
"""Async teleoperation loop with hotplug, watchdog, and safe shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time
from typing import Any

from config import Config, load_config
from controller import EV_ABS, Controller, ControllerDisconnected, InputMapper
from motor import MotorSystem

logger = logging.getLogger(__name__)


async def run_loop(
    config: Config,
    controller: Controller,
    motors: MotorSystem,
    mapper: InputMapper | None = None,
    *,
    clock=time.monotonic,
    sleep=asyncio.sleep,
    stop_event: asyncio.Event | None = None,
    max_ticks: int | None = None,
    max_i2c_errors: int = 10,
) -> int:
    """Run the input and fixed-rate control tasks until stopped or a fatal error."""
    stop = stop_event if stop_event is not None else asyncio.Event()
    mapper = mapper if mapper is not None else InputMapper(config)

    last_input = clock()
    watchdog_tripped = False
    waiting_logged = False

    async def _input_loop() -> None:
        nonlocal last_input, watchdog_tripped, waiting_logged
        while not stop.is_set():
            if not controller.connected:
                if controller.connect():
                    # Boot and reconnect start disarmed with zeroed inputs and triggers.
                    mapper.reset(disarm=True)
                    last_input = clock()
                    watchdog_tripped = False
                    waiting_logged = False
                else:
                    if not waiting_logged:
                        logger.info(
                            "Waiting for controller (%s)...",
                            ", ".join(config.controller.device_names),
                        )
                        waiting_logged = True
                    await sleep(config.controller.reconnect_interval_s)
                    continue

            try:
                async for event in controller.events():
                    if stop.is_set():
                        break
                    info = controller.absinfo(event.code) if event.type == EV_ABS else None
                    if mapper.process_event(
                        event.type, event.code, event.value, absinfo=info
                    ):
                        last_input = clock()
                        watchdog_tripped = False
                if not stop.is_set():
                    raise ControllerDisconnected("Controller event stream ended")
            except ControllerDisconnected:
                logger.warning("Controller disconnected; stopping all motors and disarming.")
                controller.disconnect()
                mapper.reset(disarm=True)
                motors.stop_all()
                waiting_logged = False
                if not stop.is_set():
                    await sleep(config.controller.reconnect_interval_s)

    async def _control_loop() -> int:
        nonlocal watchdog_tripped
        period = 1.0 / config.control_rate_hz
        next_tick = clock()
        consecutive_i2c_errors = 0
        ticks = 0

        while not stop.is_set() and (max_ticks is None or ticks < max_ticks):
            # Let the input task process everything that is already ready on this tick.
            await sleep(0)

            if (
                config.watchdog_timeout_s > 0.0
                and controller.connected
                and mapper.armed
                and clock() - last_input > config.watchdog_timeout_s
            ):
                if not watchdog_tripped:
                    logger.warning(
                        "Input watchdog timeout (%.2fs); zeroing controls.",
                        config.watchdog_timeout_s,
                    )
                    watchdog_tripped = True
                # Keep the arm state, but drop stale commands and trigger values.
                mapper.reset(disarm=False)

            try:
                if controller.connected and mapper.armed:
                    motors.apply(mapper.motor_setpoints())
                else:
                    motors.stop_all()
                consecutive_i2c_errors = 0
            except OSError:
                consecutive_i2c_errors += 1
                logger.exception(
                    "I2C error during motor update (%d/%d).",
                    consecutive_i2c_errors,
                    max_i2c_errors,
                )
                motors.stop_all()
                if consecutive_i2c_errors >= max_i2c_errors:
                    logger.error("Exceeded maximum consecutive I2C errors; exiting.")
                    stop.set()
                    return 1

            ticks += 1
            next_tick += period
            now = clock()
            if now < next_tick:
                if not stop.is_set() and (max_ticks is None or ticks < max_ticks):
                    await sleep(next_tick - now)
            else:
                next_tick = now

        return 0

    input_task = asyncio.create_task(_input_loop())
    try:
        return await _control_loop()
    finally:
        stop.set()
        input_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await input_task


async def _run_with_signals(
    config: Config,
    controller: Controller,
    motors: MotorSystem,
) -> int:
    """Run the loop with signal handlers that stop both tasks cleanly."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_handlers: dict[int, Any] = {}
    loop_handlers: list[int] = []

    def request_stop(signum: int, _frame: Any = None) -> None:
        logger.info("Received signal %s; shutting down.", signum)
        loop.call_soon_threadsafe(stop_event.set)

    signals = [signal.SIGTERM]
    if hasattr(signal, "SIGINT"):
        signals.append(signal.SIGINT)
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)

    use_loop_handlers = sys.platform != "win32" and hasattr(loop, "add_signal_handler")

    try:
        for signum in signals:
            previous_handlers[signum] = signal.getsignal(signum)
            if use_loop_handlers:
                try:
                    loop.add_signal_handler(signum, request_stop, signum)
                    loop_handlers.append(signum)
                    continue
                except (NotImplementedError, RuntimeError):
                    logger.debug(
                        "add_signal_handler unavailable for signal %s; using signal.signal.",
                        signum,
                    )
            signal.signal(signum, request_stop)

        return await run_loop(config, controller, motors, stop_event=stop_event)
    finally:
        for signum, handler in previous_handlers.items():
            try:
                if signum in loop_handlers:
                    loop.remove_signal_handler(signum)
                signal.signal(signum, handler)
            except Exception:
                logger.exception("Failed to restore handler for signal %s.", signum)


def main(config_path: str | None = None) -> int:
    """Load configuration, run the control loop, and always release hardware."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(config_path)
    except Exception:
        logger.exception("Failed to load configuration.")
        return 2

    motors: MotorSystem | None = None
    controller: Controller | None = None

    try:
        # Initialize outputs first so thrusters are stopped even without a gamepad.
        motors = MotorSystem(config)
        controller = Controller(config.controller)
        return asyncio.run(_run_with_signals(config, controller, motors))
    except KeyboardInterrupt:
        logger.info("Interrupted; shutting down.")
        return 0
    except Exception:
        logger.exception("Unhandled error in control loop.")
        return 1
    finally:
        if motors is not None:
            try:
                motors.cleanup()
            except Exception:
                logger.exception("Failed to clean up motors.")
        if controller is not None:
            try:
                controller.disconnect()
            except Exception:
                logger.exception("Failed to disconnect controller.")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))

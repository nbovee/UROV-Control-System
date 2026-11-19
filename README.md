# UROV Control System

All-IBT-2 (BTS7960) thruster control for a small ROV. A Raspberry Pi drives a
PCA9685 16-channel PWM board over I2C with `smbus2`, and a Logitech F310 or F710
gamepad provides teleoperation through `evdev`. There is no `RPi.GPIO`
dependency, so the same code runs on a Raspberry Pi 3 or a Raspberry Pi 5.

The TOML profile supports 3, 4, or more IBT-2 motors without code changes. Each
motor uses two PWM channels, so one PCA9685 can drive up to 8 motors.

## Layout

| Path | Role |
| --- | --- |
| `main.py` | Async two-task loop (`evdev.async_read_loop()` + 50 Hz control), hotplug, signal cleanup |
| `config.py` | TOML loading and validation |
| `configs/default.toml` | Default 3-motor vehicle profile |
| `controller/controller.py` | evdev discovery/grab, input normalization, trigger mux, arm state |
| `motor/pca9685.py` | Pure `smbus2` PCA9685 driver (1000 Hz, atomic all-off) |
| `motor/motor.py` | One IBT-2 motor on two PWM channels |
| `motor/system.py` | Named motor setpoints, stop-all and cleanup |
| `tests/` | Config, controller, driver, and loop tests with fake hardware |

## Hardware

All thrusters use IBT-2 (BTS7960) drivers. The default profile maps PCA9685
channels to the IBT-2 `R_PWM` / `L_PWM` inputs as follows:

| Thruster | Config name | RPWM channel | LPWM channel |
| --- | --- | ---: | ---: |
| Left | `left` | 2 | 4 |
| Right | `right` | 0 | 5 |
| Vertical | `vertical` | 1 | 3 |

Verify every channel against the carrier PCB pinout before connecting thruster
power. The table is only the default profile: add another `[[motor]]` block with
unused channels to drive a fourth (or later) motor, no Python changes required.
A commented fourth-motor example is at the bottom of `configs/default.toml`.

### Carrier PCB enable pins

On the carrier PCB, `R_EN` and `L_EN` are tied HIGH. The IBT-2 half-bridges are
therefore always enabled whenever logic power is present, and only the PWM
inputs control the output. Software compensates by zeroing all 16 PCA9685
channels atomically (`ALL_LED_OFF_H`) on boot, disarm, controller disconnect,
and shutdown. When a motor reverses, the inactive PWM input is zeroed before the
active input is driven to avoid shoot-through.

Because enable is hard-wired, a software crash or I2C fault cannot disable the
drivers in hardware. Add a hardware fail-safe when the vehicle is built out: pull
the PCA9685 `/OE` pin high so outputs are disabled by default, or wire an inline
e-stop switch into the IBT-2 logic-power or motor-supply path.

### I2C and permissions

The PCA9685 is expected at address `0x40` on `/dev/i2c-1` and runs at 1000 Hz.
Enable I2C (`raspi-config`, or `dtparam=i2c_arm=on` in `config.txt`) and add the
service user to the `i2c` and `input` groups:

```bash
sudo usermod -aG i2c,input "$USER"
```

Log out and back in after changing groups. `evdev` needs read access to
`/dev/input/event*`.

## Controls

Use the F310/F710 in XInput mode (the F710 has a switch on the back; set it to
`X`). Bindings come from `configs/default.toml`.

| Input | Action |
| --- | --- |
| Left stick Y | Left thruster |
| Right stick Y | Right thruster |
| Left / Right triggers | Vertical thruster, muxed in the input layer to `[-1.0, +1.0]` |
| **A** (Green) | Arm — only accepted while all motor controls are neutral |
| **B** (Red) | Disarm / E-Stop |
| **LB** / **RB** / **Y** | Flip left / right / vertical direction |
| **Back** | Reset all flips |

Stick axes are normalized to `[-1.0, +1.0]` in the input layer. The vertical
control is the left-trigger value minus the right-trigger value, so the left
trigger drives positive and the right trigger drives negative. Arm state is
separate from the flips: flipping a direction does not re-arm the vehicle.

## Runtime behavior

- Starts safely with no gamepad connected. All PCA9685 outputs stay off while
  the loop polls for a configured device.
- Grabs a matching F310/F710 automatically when it appears and ignores other
  input devices.
- Handles USB unplug/replug and F710 battery sleep/wake: a disconnect disarms,
  zeroes all outputs, releases the device, and resumes the async connect loop.
- Handles `Ctrl-C` (`SIGINT`), `SIGTERM`, and `SIGHUP`. All three run the same
  cleanup path: stop all motors, zero every PCA9685 channel, put the PCA9685 to
  sleep, close the I2C bus, and release the gamepad.
- Runs two `asyncio` tasks on one event loop: an input task consumes `evdev`'s
  `async_read_loop()`, and a separate 50 Hz control task applies setpoints every
  tick, so output is refreshed even when no new events arrive. The default local
  `evdev` profile uses `watchdog_timeout_s = 0.0` because input arrives directly
  on that loop. The same loop is the foundation for the future PoE network
  tasks.

## Install and run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Hardware operation
requires Linux on a Raspberry Pi; on Windows or x86 Linux the Pi dependencies
are skipped and the tests still run.

```bash
uv sync --extra dev
uv run python main.py
uv run python main.py configs/default.toml
```

`main.py` loads `configs/default.toml` by default. A missing or invalid
configuration exits with code 2; a runtime failure exits with code 1.

## Configuration

Edit `configs/default.toml` or pass another profile path.

- `[controller]` — `device_names` (the F310/F710 names accepted by `evdev`),
  `deadzone` (default `0.02`), and `reconnect_interval_s` (hotplug poll cadence,
  default `0.5`).
- `[controller.axes]` — evdev `ABS` code to label. The default binds `1 = "L_Y"`,
  `2 = "L_trig"`, `4 = "R_Y"`, and `5 = "R_trig"`. Code `0 = "L_X"` is present
  but commented out for an optional fourth motor.
- `[controller.buttons]` — evdev key code to label (`304` A, `305` B, `308` Y,
  `310` LB, `311` RB, `314` Back).
- `[controller.actions]` — which labels mean `arm`, `disarm`, and
  `reset_flips`.
- `[controller.trigger_mux]` — maps a virtual control to two axis labels, for
  example `vertical = ["L_trig", "R_trig"]` (left positive, right negative).
  The source axes are consumed by the mux and cannot also drive a motor
  directly.
- `[vehicle]` — `i2c_bus` (`1`), `pca9685_address` (`0x40`), `pwm_frequency`
  (`1000`), `control_rate_hz` (`50.0`), and `watchdog_timeout_s`.
  Keep `watchdog_timeout_s = 0.0` for the local evdev input task. Set it to
  `0.5` when controller state arrives over the future PoE heartbeat link, so a
  lost link zeros the outputs.
- `[[motor]]` — one block per motor:
  - `name` — unique motor name.
  - `rpwm` / `lpwm` — PCA9685 channels `0..15`, globally unique across motors.
  - `max_duty` — thrust fraction in `(0.0, 1.0]`; the duty applied at full stick.
  - `control` — axis label or trigger-mux name that drives the motor.
  - `flip` — optional button label that toggles direction.
  - `deadzone` — `true` for stick axes, `false` for muxed triggers.
  - `invert` — `true` to reverse electrical direction in software.

Validation rejects duplicate motor names or PWM channels, unknown control or
flip labels, and arm/disarm/reset labels that collide with flips.

## Safety & Development

Safety expectations:

- Disconnect battery power before touching thrusters or wiring.
- Outputs are zeroed on boot, disarm, disconnect, and shutdown.
- `B` is the software E-Stop; arming requires neutral controls.
- `max_duty` limits thrust per motor, and duty is clamped to the PCA9685 range.
- I2C errors are logged and cleanup continues past individual failures.
- There is intentionally no slew-rate limiting, so the thrusters follow the
  sticks immediately.
- These behaviours are covered with fake hardware, not on the physical ROV.
  Bench-test with propellers removed.

Development commands:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

CI runs the same lint and test commands on `ubuntu-latest`.

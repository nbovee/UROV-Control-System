# UROV Control System

Raspberry Pi (Pi 3 & Pi 5, no `RPi.GPIO`) control system for all-IBT-2 (BTS7960) ROV thrusters, driven via a PCA9685 over I2C (`smbus2`) and a Logitech F310/F710 gamepad (`evdev` + `asyncio`). Configured by TOML (`configs/default.toml`), with up to 8 IBT-2 motors per PCA9685 (two PWM channels each).

## Quick Start

```bash
# Enable I2C, then grant device access (log out/in afterwards)
sudo raspi-config          # Interface Options → I2C → Enable
sudo usermod -aG i2c,input "$USER"

uv sync --extra dev
uv run python main.py                    # uses configs/default.toml
uv run python main.py path/to/config.toml
```

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). It boots safely with no gamepad (outputs stay off), hotplugs and reconnects automatically, and shuts down cleanly on `SIGINT`/`SIGTERM`/`SIGHUP`.

## Controls

F310/F710 in **XInput mode** (F710: rear switch to `X`).

| Input | Action |
| --- | --- |
| Left / Right stick Y | Left / Right thruster |
| Left / Right triggers | Vertical thruster (muxed to `[-1, 1]`) |
| **A** | Arm (requires neutral controls) |
| **B** | Disarm / E-Stop |
| **LB** / **RB** / **Y** | Flip left / right / verticals |
| **D-pad Left / Right** | Trim left/right balance |
| **D-pad Up / Down** | Trim vertical balance |
| **Back** | Reset flips & trim (trim does not persist through a reset) |

## Hardware & Safety

Default PCA9685 (`/dev/i2c-1`, `0x40`, 1000 Hz) channel map — verify against the carrier PCB pinout before powering thrusters:

| Thruster | `name` | RPWM | LPWM |
| --- | --- | ---: | ---: |
| Left | `left` | 2 | 4 |
| Right | `right` | 0 | 5 |
| Vertical (primary) | `vertical_primary` | 1 | 3 |
| Vertical (secondary) | `vertical_secondary` | 6 | 7 |

Both vertical thrusters share `control = "vertical"` and `flip = "Y"`, so they drive in sync (set `invert = true` on one if counter-rotating).

- **Carrier PCB:** `R_EN`/`L_EN` are tied HIGH, so bridges stay enabled whenever logic power is present. Software zeroes all channels atomically (`ALL_LED_OFF_H`) on boot, disarm, disconnect, and exit; reversing also zeroes the inactive PWM input to avoid shoot-through.
- A crash or I2C fault therefore cannot disable the drivers — add a hardware fail-safe (pull `/OE` high, or an inline e-stop in the logic/motor supply).
- Bench-test with propellers removed; disconnect the battery before wiring.

## Configuration & Development

Edit `configs/default.toml` (or pass another profile):

- `[controller]` — device names, deadzone, reconnect cadence, axis/button/action bindings; `[controller.balance.*]` maps a D-pad axis to its two thrusters (`axis`, `primary`, `secondary`, `step`, `max_trim`).
- `[vehicle]` — `i2c_bus`, `pca9685_address`, `pwm_frequency`, `control_rate_hz`. Keep `watchdog_timeout_s = 0.0` for the local evdev loop; set `0.5` for a future PoE heartbeat link so a lost link zeros outputs.
- `[[motor]]` — one block per thruster (`rpwm`/`lpwm`, `max_duty`, `control`, `flip`, `invert`); a commented fourth-motor example is included.

```bash
uv run pytest
uv run ruff check .
```

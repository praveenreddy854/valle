# Valle

Valle is a standalone Raspberry Pi robot-car webhook service for Siri Shortcuts. It controls two DC motors through an L298N driver using `gpiozero`.

## Wiring

Use BCM GPIO numbering.

| L298N pin | Raspberry Pi GPIO |
| --- | --- |
| ENA | GPIO12 |
| IN1 | GPIO5 |
| IN2 | GPIO6 |
| ENB | GPIO13 |
| IN3 | GPIO20 |
| IN4 | GPIO21 |

Connect the Pi ground to the L298N ground. Power the motors from a separate motor supply, not from the Pi. Remove the ENA/ENB jumpers if your L298N board has them, so PWM speed control can use GPIO12 and GPIO13.

## Install on Raspberry Pi

```bash
cd /home/pi/valle
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
VALLE_DRIVER=gpiozero python -m valle.app
```

The server listens on port `8080` by default.

```text
http://rpi.local:8080/forward
http://rpi.local:8080/backward
http://rpi.local:8080/left
http://rpi.local:8080/right
http://rpi.local:8080/stop
```

Each movement command is momentary and stops automatically after at most 5 seconds. Left and right are pivot turns.

## Siri Shortcuts

Create one Shortcut per command:

1. Add the "Get Contents of URL" action.
2. Set the method to `GET`.
3. Use one of the URLs above.
4. Name the Shortcut, for example "Valle forward".

Optional query parameters:

```text
http://rpi.local:8080/forward?speed=40
http://rpi.local:8080/left?speed=50&duration=1.5
```

`speed` is a percentage from `0` to `100`. `duration` is in seconds and is clamped to the configured maximum, which defaults to `5`.

## Other endpoints

```text
GET /drive?command=forward
GET /drive/forward
GET /status
GET /health
```

The simple command routes and the generic `/drive` routes accept both `GET` and `POST`. For JSON `POST` requests, send fields such as `command`, `speed`, and `duration`.

## Local testing without GPIO

On a Mac or other non-Pi machine, the default `auto` driver uses the mock motor driver.

```bash
python -m valle.app
curl "http://127.0.0.1:8080/forward?duration=0.5&speed=30"
curl "http://127.0.0.1:8080/status"
```

You can force mock mode with:

```bash
VALLE_DRIVER=mock python -m valle.app
```

## Configuration

| Environment variable | Default |
| --- | --- |
| `VALLE_HOST` | `0.0.0.0` |
| `VALLE_PORT` | `8080` |
| `VALLE_DRIVER` | `auto` |
| `VALLE_DEFAULT_SPEED_PERCENT` | `60` |
| `VALLE_DEFAULT_DURATION_SECONDS` | `5` |
| `VALLE_MAX_DURATION_SECONDS` | `5` |
| `VALLE_LEFT_FORWARD_PIN` | `5` |
| `VALLE_LEFT_BACKWARD_PIN` | `6` |
| `VALLE_LEFT_ENABLE_PIN` | `12` |
| `VALLE_RIGHT_FORWARD_PIN` | `20` |
| `VALLE_RIGHT_BACKWARD_PIN` | `21` |
| `VALLE_RIGHT_ENABLE_PIN` | `13` |

Before testing on the floor, lift the car so the wheels can spin freely and verify `/stop` works.

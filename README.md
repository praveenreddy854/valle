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

Install a GPIO backend for `gpiozero`:

```bash
sudo apt update
sudo apt install -y python3-lgpio
```

```bash
cd /home/pi/valle
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -r requirements.txt
GPIOZERO_PIN_FACTORY=lgpio VALLE_DRIVER=gpiozero python3 -m valle.app
```

If you already created the virtual environment without `--system-site-packages`, recreate it or install `lgpio` inside the virtual environment with `pip install lgpio`.

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
| `VALLE_TURN_DURATION_SECONDS` | `0.25` |
| `VALLE_AUTOPILOT_MAX_SECONDS` | `1800` |
| `VALLE_AUTOPILOT_IDLE_SECONDS` | `20` |
| `VALLE_LEFT_FORWARD_PIN` | `5` |
| `VALLE_LEFT_BACKWARD_PIN` | `6` |
| `VALLE_LEFT_ENABLE_PIN` | `12` |
| `VALLE_RIGHT_FORWARD_PIN` | `20` |
| `VALLE_RIGHT_BACKWARD_PIN` | `21` |
| `VALLE_RIGHT_ENABLE_PIN` | `13` |

Before testing on the floor, lift the car so the wheels can spin freely and verify `/stop` works.

## Autopilot (off-device brain)

Autopilot runs as a **separate service on a more powerful machine** (e.g., a Mac mini). The Pi only owns the session, the safety watchdogs, and the motors. The brain pulls MJPEG frames from the Pi, runs monocular depth, and sends drive commands back inside an `/autopilot` session.

See `docs/adr/0001-reflex-uses-depth-not-object-detection.md` for why perception is depth-based rather than object-detection-based.

### Pi side: camera streamer

A separate process exposes the Pi camera as MJPEG so the streaming connection cannot block the motor controller:

```bash
sudo apt install -y python3-picamera2
VALLE_CAMERA_PORT=8081 python3 -m valle.camera
```

| Environment variable | Default |
| --- | --- |
| `VALLE_CAMERA_HOST` | `0.0.0.0` |
| `VALLE_CAMERA_PORT` | `8081` |
| `VALLE_CAMERA_WIDTH` | `640` |
| `VALLE_CAMERA_HEIGHT` | `480` |
| `VALLE_CAMERA_FPS` | `10` |

Stream URL: `http://rpi.local:8081/stream.mjpg`.

### Pi side: autopilot endpoints

While a session is active, all Siri-facing endpoints (`/forward`, `/left`, …) return `409` — only `/stop` is still honored as a panic button (and ends the session).

```text
POST /autopilot/start
   body (optional): {"max_seconds": 1800, "idle_seconds": 20}
   201 {"session_id": "<token>", "max_seconds": ..., "idle_seconds": ..., "started_at": ...}
   409 if a session is already active

POST /autopilot/<session_id>/drive
   body: {"direction": "forward"|"backward"|"left"|"right", "duration": 0.3, "speed": 60}
   200 {"ok": true, ...session telemetry}
   409 if the session is not active

POST /autopilot/<session_id>/stop
   body (optional): {"reason": "manual"|"blind"}
   200 {"ok": true, "ended_reason": "autopilot_<reason>"}
```

The session ends automatically:

- After `VALLE_AUTOPILOT_MAX_SECONDS` (hard cap).
- After `VALLE_AUTOPILOT_IDLE_SECONDS` with no `forward` or `backward` command (idle watchdog — pivoting in place does not count as progress).
- On `/stop` from any source.

### Mac side: install and run the brain

On the Mac (or any non-Pi host):

```bash
cd valle
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[brain]

export VALLE_PI_BASE_URL=http://rpi.local:8080
export VALLE_CAMERA_URL=http://rpi.local:8081/stream.mjpg
python -m valle.brain
```

On first run the Depth Anything V2 Small model is downloaded from Hugging Face (~50 MB).

| Environment variable | Default |
| --- | --- |
| `VALLE_PI_BASE_URL` | `http://rpi.local:8080` |
| `VALLE_CAMERA_URL` | `http://rpi.local:8081/stream.mjpg` |
| `VALLE_BRAIN_TICK_HZ` | `4` |
| `VALLE_BRAIN_GRACE_SECONDS` | `2` |
| `VALLE_DEPTH_MODEL` | `depth-anything/Depth-Anything-V2-Small-hf` |
| `VALLE_DEPTH_DEVICE` | `auto` (uses MPS on Apple Silicon, else CPU) |
| `VALLE_BLOCKED_THRESHOLD` | `0.55` |
| `VALLE_HYSTERESIS_MARGIN` | `0.05` |
| `VALLE_PULSE_FORWARD` / `VALLE_PULSE_TURN` / `VALLE_PULSE_BACKWARD` | `0.30` / `0.20` / `0.30` |
| `VALLE_SPEED_FORWARD` / `VALLE_SPEED_TURN` / `VALLE_SPEED_BACKWARD` | `55` / `55` / `45` |

The thresholds and pulse durations almost certainly need tuning on the bench — start with the wheels off the ground.

## Object find (off-device)

A second off-device service answers text-queried object lookups. It dials a WebSocket into the Pi and stays connected; the Pi exposes `/find?object=<text>` and proxies the request over that socket. The Mac is never directly addressable.

On the Mac:

```bash
make find
```

On any client (Siri Shortcut, curl, browser):

```bash
curl "http://rpi.local:8080/find?object=toy"
# {"id":"...","type":"find_result","object":"toy","found":true,
#  "results":[{"score":0.42,"label":"toy","box":{"xmin":...}}],
#  "capture_seconds":0.34}

# When the find service is not running on the Mac:
# 503 {"ok":false,"error":"brain offline"}
```

| Environment variable | Default |
| --- | --- |
| `VALLE_PI_WS_URL` | `ws://rpi.local:8080/brain/find` |
| `VALLE_CAMERA_URL` | `http://rpi.local:8081/stream.mjpg` |
| `VALLE_DETECTOR_MODEL` | `google/owlv2-base-patch16-ensemble` |
| `VALLE_DETECTOR_DEVICE` | `auto` |
| `VALLE_SCORE_THRESHOLD` | `0.10` |
| `VALLE_MAX_RESULTS` | `5` |
| `VALLE_FIND_TIMEOUT_SECONDS` | `10` (Pi-side; how long the Pi waits for a brain response) |

`make find` runs independently of `make brain` (autopilot). You can run both, just one, or neither.

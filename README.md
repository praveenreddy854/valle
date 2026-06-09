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

Valle requires Python 3.12. If you already have an older `.venv`, recreate it
with Python 3.12 before running services.

Install a GPIO backend for `gpiozero`:

```bash
sudo apt update
sudo apt install -y python3-lgpio
```

```bash
cd /home/pi/valle
python3.12 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -r requirements.txt
GPIOZERO_PIN_FACTORY=lgpio VALLE_DRIVER=gpiozero python -m valle.app
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

### Pi side: agent sessions

Agent sessions are for scheduled inspection jobs such as "check the back door
lock at 10 PM" or "look for vacuum blockers before the robot vacuum starts."
The agent plans the mission, but it does not command the motors directly. Each
movement request is a short **intent** that passes through Valle's reflex gate
before a motor pulse is executed.

```text
POST /agent/start
   body: {
     "goal": "check the back door lock",
     "task": "nightly_door_lock_check",
     "skill": "check_door_locks",
     "targets": ["back_door"],
     "max_seconds": 300,
     "idle_seconds": 20
   }
   201 {"session_id": "<token>", "kind": "agent", "mission": {...}, ...}

POST /agent/reflex
   body: {"left": 0.2, "center": 0.3, "right": 0.4, "source": "depth"}
   200 {"ok": true, "clearance": {...}}

POST /agent/<session_id>/intent
   body: {
     "type": "drive_pulse",
     "direction": "forward"|"backward"|"left"|"right",
     "duration": 0.25,
     "speed": 35,
     "reason": "approach back door inspection spot"
   }
   200 {"ok": true, "executed": true|false, "reflex": {...}, ...}

POST /agent/<session_id>/observe
   200 {"ok": true, "agent": {...}, "status": {...}, "reflex": {...}}

POST /agent/<session_id>/intent
   body: {"type": "stop", "reason": "manual"}
   200 {"ok": true, "ended_reason": "agent_manual"}
```

If there is no fresh reflex reading, agent movement is vetoed with
`executed: false`. Forward movement requires a clear center strip, pivot turns
require the corresponding side strip to be clear, and bounded reverse pulses
are allowed as an escape action once a fresh reading exists. Manual `/stop`
still ends the session immediately.

### Mac side: CrewAI agent runner

The agent loop lives off-device in `valle.brain.agent`. It uses CrewAI with
Azure OpenAI, while Valle's Pi still gates movement through `/agent/*`.

Install the CrewAI agent extra:

```bash
make install-agent
```

Valle uses Python 3.12. If `python3.12` is not your default interpreter, pass it
explicitly:

```bash
VENV_PY=python3.12 make install-agent
```

Run a one-off inspection mission:

```bash
# Fill .env with AZURE_* and VALLE_PI_BASE_URL values.
.venv/bin/python -m valle.brain.agent "check the back door lock"
```

Scheduled jobs can pass richer mission metadata:

```dotenv
VALLE_AGENT_MISSION_JSON='{
  "goal": "check the back door lock",
  "task": "nightly_door_lock_check",
  "skill": "check_door_locks",
  "targets": ["back_door"],
  "max_seconds": 300,
  "idle_seconds": 20
}'
```

```bash
make agent
```

The runner exposes CrewAI tools for starting a session, observing the Pi status,
requesting reflex-gated drive pulses, stopping the session, and recording the
inspection result. It does not call motor endpoints directly.

### Mac side: brain API

The brain can also run as an HTTP API on the Mac:

```bash
make install-brain-api
make brain-api
```

Default URL: `http://127.0.0.1:8090`.

Optional settings:

```dotenv
VALLE_BRAIN_API_HOST=0.0.0.0
VALLE_BRAIN_API_PORT=8090
```

Run a CrewAI agent mission over HTTP:

```bash
curl -X POST "http://127.0.0.1:8090/agent/run" \
  -H "content-type: application/json" \
  -d '{
    "goal": "check the back door lock",
    "task": "nightly_door_lock_check",
    "skill": "check_door_locks",
    "targets": ["back_door"]
  }'
```

Run brain-owned find or seek over HTTP:

```bash
curl -X POST "http://127.0.0.1:8090/find" \
  -H "content-type: application/json" \
  -d '{"object": "toy"}'

curl -X POST "http://127.0.0.1:8090/seek" \
  -H "content-type: application/json" \
  -d '{"object": "toy", "max_seconds": 30}'
```

The brain API is separate from the Pi motor API. Movement still goes through the
Pi's session and reflex gates.

### Mac side: install and run the brain

On the Mac (or any non-Pi host):

```bash
cd valle
python3.12 -m venv .venv
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

## Brain object find and seek (off-device)

A brain-owned off-device service answers text-queried object lookups. It dials a WebSocket into the Pi and stays connected; the Pi exposes `/find?object=<text>` and proxies the request over that socket. The Mac is never directly addressable.

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

`make find` runs the brain-owned object find service independently of `make brain` (autopilot). You can run both, just one, or neither.

### `/seek` — drive around until found

`/seek` reuses the same brain (and the same depth + reflex stack the autopilot uses) but adds detection on every tick. The car reflex-drives until either OWLv2 spots the target above the seek threshold or `max_seconds` is reached.

```bash
curl "http://rpi.local:8080/seek?object=toy"
# starts an autopilot session, reflex-drives the room, detects every tick
# {"id":"...","type":"seek_result","object":"toy","found":true,
#  "score":0.42,"label":"toy","box":{...},
#  "elapsed_seconds":12.4,"ticks":47,"reason":"found"}
# or, on timeout:
# {"id":"...","type":"seek_result","object":"toy","found":false,
#  "elapsed_seconds":60.0,"ticks":210,"reason":"max_seconds"}
```

Override the deadline per request (Pi clamps to `VALLE_SEEK_MAX_SECONDS`):

```bash
curl "http://rpi.local:8080/seek?object=toy&max_seconds=30"
```

| Environment variable | Default | Lives on |
| --- | --- | --- |
| `VALLE_SEEK_MAX_SECONDS` | `60` | Pi (hard cap) |
| `VALLE_SEEK_TIMEOUT_BUFFER_SECONDS` | `10` | Pi (extra HTTP wait beyond max_seconds) |
| `VALLE_SEEK_FOUND_SCORE` | `0.20` | Mac (confidence required to count as found) |
| `VALLE_SEEK_DEFAULT_MAX_SECONDS` | `60` | Mac (used when request omits max_seconds) |
| `VALLE_SEEK_PULSE_SECONDS` | `4.0` | Mac (long-pulse duration refreshed every tick so motion is continuous; must be ≤ Pi's `VALLE_MAX_DURATION_SECONDS`) |

Seek requires `make find` to be running on the Mac (it loads OWLv2) **and** that the existing autopilot brain stack is installed (it shares depth + reflex code with `make brain`). You do not need to run `make brain` at the same time as `make find`; `/seek` starts and ends its own autopilot session over the same Pi API.

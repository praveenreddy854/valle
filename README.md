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

## Logs and telemetry

Every runnable Valle service configures file logging and OpenTelemetry tracing.
By default:

```text
logs/<service>.log
logs/<service>.traces.jsonl
```

Useful settings:

| Environment variable | Default |
| --- | --- |
| `VALLE_LOG_DIR` | `logs` |
| `VALLE_LOG_LEVEL` | `INFO` |
| `VALLE_LOG_CONSOLE` | `true` |
| `VALLE_LOG_MAX_BYTES` | `10000000` |
| `VALLE_LOG_BACKUP_COUNT` | `5` |
| `VALLE_LOG_SUPPRESS_PORTAL_POLLING` | `true` |
| `VALLE_VERBOSE_SDK_LOGS` | `false` |
| `VALLE_OTEL_ENABLED` | `true` |
| `VALLE_OTEL_TRACES_FILE` | `logs/<service>.traces.jsonl` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset; when set, traces are also exported over OTLP/HTTP |

OpenTelemetry instruments Flask routes and outbound `requests` calls when the
instrumentation packages are installed. Local trace files are JSONL so they can
be inspected without running a collector.

Successful portal polling requests are suppressed from the log by default so
the portal does not flood the request log it is displaying. Set
`VALLE_LOG_SUPPRESS_PORTAL_POLLING=false` to include those requests. Azure,
CrewAI, and `httpx` internals log warnings and errors by default; set
`VALLE_VERBOSE_SDK_LOGS=true` when debugging SDK transport details.

## Brain web portal

Run the brain API and open the portal:

```bash
make brain-api
open http://127.0.0.1:8090/portal
```

The portal shows:

- The Pi camera feed through the brain API at `/portal/camera.mjpg`.
- Available local log and trace files from `VALLE_LOG_DIR`.
- Recent JSON log lines and OpenTelemetry span records.
- The configured `OTEL_EXPORTER_OTLP_ENDPOINT`, with URL credentials masked.

Portal settings:

| Environment variable | Default |
| --- | --- |
| `VALLE_PORTAL_CAMERA_URL` | `VALLE_CAMERA_URL` if set, otherwise `http://rpi.local:8081/stream.mjpg` |
| `VALLE_LOG_DIR` | `logs` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset |

Example `.env` values:

```text
VALLE_PORTAL_CAMERA_URL=http://rpi.local:8081/stream.mjpg
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
```

If your collector expects the base OTLP endpoint instead, use:

```text
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
```

## Autopilot (off-device brain)

Autopilot runs **off-device on a more powerful machine** (e.g., a Mac mini) as
part of the brain API. The Pi only owns the session, the safety watchdogs, and
the motors. The brain pulls MJPEG frames from the Pi, runs monocular depth, and
sends drive commands back inside an `/autopilot` session. You start and stop it
through the brain API rather than as a standalone process (see
[Mac side: brain API](#mac-side-brain-api)).

See `docs/adr/0001-reflex-uses-depth-not-object-detection.md` for why perception is depth-based rather than object-detection-based.

### Pi side: camera

`make app` runs the camera in the **same process** as the motor controller. The
MJPEG server still listens on its own port (and its own thread), so a streaming
client can never block a motor command:

```bash
sudo apt install -y python3-picamera2
make app
```

If picamera2 is not installed (e.g. when running off the Pi), the camera is
skipped and the motor API still starts.

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

Install the brain API extras (CrewAI agent + depth/detector):

```bash
make install-brain-api
```

Valle uses Python 3.12. If `python3.12` is not your default interpreter, pass it
explicitly:

```bash
VENV_PY=python3.12 make install-brain-api
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
requesting reflex-gated drive pulses, checking whether an object is visible in
the camera (`find_object`, with a left/center/right position hint), saving
evidence frames (`capture_evidence`), stopping the session, and recording the
inspection result. It does not call motor endpoints directly.

While a mission runs, the runner also feeds the Pi's reflex gate: a background
perception loop pulls camera frames from `VALLE_CAMERA_URL`, runs monocular
depth, and posts clearance readings to `POST /agent/reflex` at
`VALLE_BRAIN_TICK_HZ`. Without those readings every movement intent would be
vetoed with `no_reflex_reading`. This is why `make install-brain-api` installs the
brain extras alongside CrewAI.

### Mac side: brain API

The brain API is the single Mac-side service. It serves find/seek, agent
missions, the mission scheduler, the portal, and autopilot:

```bash
make install-brain-api
make brain-api
```

Default URL: `http://127.0.0.1:8090`.

Start and stop continuous autopilot driving through it:

```bash
curl -X POST "http://127.0.0.1:8090/autopilot/start"
curl        "http://127.0.0.1:8090/autopilot/status"
curl -X POST "http://127.0.0.1:8090/autopilot/stop"
```

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

Schedule a recurring mission (daily, 24-hour `HH:MM`):

```bash
curl -X POST "http://127.0.0.1:8090/missions" \
  -H "content-type: application/json" \
  -d '{"schedule": "22:00", "goal": "check the back door lock", "targets": ["back_door"]}'

curl "http://127.0.0.1:8090/missions"
curl -X DELETE "http://127.0.0.1:8090/missions/<mission_id>"
```

Inspect mission history and evidence images:

```bash
curl "http://127.0.0.1:8090/runs"
curl "http://127.0.0.1:8090/runs/<run_id>"
open "http://127.0.0.1:8090/evidence/<run_id>/01-back_door_wide.jpg"
```

Every agent run — CLI, `/agent/run`, or scheduled — appends a record to
`VALLE_RUNS_FILE` with the mission, status, the recorded inspection result, and
the evidence frames the agent saved with its `capture_evidence` tool. When
`VALLE_NOTIFY_URL` is set, each finished mission posts a plain-text summary with
a `Title` header; point it at an ntfy topic (for example
`https://ntfy.sh/valle-home`) to get phone pushes.

| Environment variable | Default |
| --- | --- |
| `VALLE_RUNS_FILE` | `logs/agent-runs.jsonl` |
| `VALLE_EVIDENCE_DIR` | `evidence` |
| `VALLE_MISSIONS_FILE` | `missions.json` |
| `VALLE_SCHEDULER_POLL_SECONDS` | `20` |
| `VALLE_NOTIFY_URL` | unset |

The brain API is separate from the Pi motor API. Movement still goes through the
Pi's session and reflex gates.

### Mac side: install and run the brain

Autopilot is part of the brain API. Install once, run the API, then start and
stop autopilot through it:

```bash
cd valle
make install-brain-api

export VALLE_PI_BASE_URL=http://rpi.local:8080
export VALLE_CAMERA_URL=http://rpi.local:8081/stream.mjpg
make brain-api

# in another shell:
curl -X POST http://127.0.0.1:8090/autopilot/start
curl      http://127.0.0.1:8090/autopilot/status
curl -X POST http://127.0.0.1:8090/autopilot/stop
```

Starting a `/seek` or agent mission while autopilot is running automatically
pauses autopilot for the duration and resumes it afterward (the Pi grants one
motor session at a time).

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

> Most users should use the brain API's `/find` and `/seek` on port 8090
> (see [Mac side: brain API](#mac-side-brain-api)). The path below is the
> optional **Siri-facing** route: it lets a Shortcut hit the Pi directly at
> `http://rpi.local:8080/find`, which the Pi proxies to the Mac over a
> WebSocket. The Mac is never directly addressable.

A brain-owned off-device service answers text-queried object lookups. It dials a WebSocket into the Pi and stays connected; the Pi exposes `/find?object=<text>` and proxies the request over that socket.

On the Mac:

```bash
python -m valle.brain.find
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

`python -m valle.brain.find` runs the Siri-facing object find service. It is independent of the brain API and of autopilot.

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

This Siri-facing `/seek` requires `python -m valle.brain.find` running on the Mac (it loads OWLv2 and shares the depth + reflex stack). It starts and ends its own autopilot session over the Pi API, so you do not run autopilot separately at the same time.

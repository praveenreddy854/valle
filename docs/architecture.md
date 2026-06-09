# Valle Architecture

Valle is a small Raspberry Pi robot car with a local motor controller, a camera
streamer, and off-device services for perception and task reasoning. The core
design rule is that physical movement remains bounded and interruptible even
when an AI agent is planning a scheduled inspection task.

## Runtime Roles

```text
Client / Scheduler
  -> Pi control server
       -> ValleController
            -> ReflexGate
            -> MotorDriver
       -> BrainBridge
  -> Camera server
  -> Brain API

Off-device services
  -> Brain HTTP API
  -> Reflex/perception updater
  -> Brain agent mission runner (CrewAI + Azure OpenAI)
  -> Brain object find / seek service
```

The Pi is authoritative for motor safety. Off-device services can request work,
send perception readings, or ask for bounded movement, but they do not directly
drive GPIO pins.

## Brain API

The Mac-side brain API runs with `python -m valle.brain.api` or `make brain-api`
and listens on port `8090` by default.

It exposes:

- `GET /health`
- `POST /agent/run`
- `POST /find`
- `POST /seek`

These endpoints are brain-facing entry points for schedulers or other local
clients. They do not bypass the Pi control server; movement still goes through
Pi sessions and reflex-gated intents.

## Pi Control Server

The Flask app in `valle/app.py` exposes the robot control API.

- Manual movement endpoints such as `/forward`, `/left`, and `/stop`.
- Autopilot endpoints under `/autopilot/*`.
- Agent inspection endpoints under `/agent/*`.
- Object lookup endpoints `/find` and `/seek`, proxied through `BrainBridge`.

The Pi process owns:

- Session locking.
- Short movement pulse execution.
- Maximum movement duration clamping.
- Hard session time caps.
- Idle/no-progress watchdogs.
- Panic stop behavior.

## Session Types

Valle has one active session slot. Starting an agent session while autopilot is
running fails, and starting autopilot while an agent session is running also
fails.

### Autopilot Session

An autopilot session is reflex driving. The off-device brain reads camera
frames, chooses a direct drive action, and sends it through `/autopilot`.

This mode has no destination, map, or inspection goal. It exists to keep Valle
moving cautiously based on camera/depth input.

### Agent Session

An agent session is for scheduled or user-requested inspection missions:

- Check door locks at 10 PM.
- Scout the floor before a robot vacuum starts.
- Inspect stove knobs.
- Check pet bowls.

The agent plans the mission, but every movement is a movement intent. A movement
intent becomes a motor pulse only after the local reflex gate authorizes it.

The agent loop itself lives in `valle.brain.agent` and uses CrewAI with Azure
OpenAI. Object find and seek live in `valle.brain.find`. CrewAI owns the
agent/task orchestration. Valle owns the tools that touch the robot.

## Reflex Gate

`valle/reflex.py` contains `ReflexGate`, which stores the latest normalized
clearance reading:

```json
{
  "left": 0.2,
  "center": 0.3,
  "right": 0.4,
  "source": "depth"
}
```

Higher values mean closer and therefore more blocked.

Movement authorization rules:

- No fresh reading: reject movement.
- Stale reading: reject movement.
- Forward: require center strip to be clear.
- Left pivot: require left strip to be clear.
- Right pivot: require right strip to be clear.
- Backward: allow as a bounded escape action when a fresh reading exists.

The reflex gate returns structured reasons such as `center_blocked`,
`center_clear`, `no_reflex_reading`, or `stale_reflex_reading`. When blocked, it
also recommends a safer direction when possible.

## Agent Flow

Scheduled task example: check the back door lock at 10 PM.

```text
1. Scheduler starts an agent session.
2. Agent loads the task plan and known inspection spot.
3. Perception service posts fresh clearance to /agent/reflex.
4. Agent proposes a short drive_pulse intent.
5. ReflexGate authorizes or vetoes the intent.
6. ValleController executes only authorized bounded motor pulses.
7. Agent observes status and camera evidence.
8. Agent repeats until the inspection viewpoint is reached.
9. Vision reasoning checks the lock state from evidence images.
10. Agent reports state, confidence, and evidence.
11. Agent stops the session.
```

API shape:

```text
POST /agent/start
POST /agent/reflex
POST /agent/<session_id>/intent
POST /agent/<session_id>/observe
```

CrewAI tools exposed by the agent runner:

- `start_agent_session`: calls `POST /agent/start`.
- `observe`: calls `POST /agent/<session_id>/observe`.
- `drive_pulse`: calls `POST /agent/<session_id>/intent` with `type:
  drive_pulse`.
- `stop_agent_session`: calls `POST /agent/<session_id>/intent` with `type:
  stop`.
- `record_inspection_result`: records the mission result for scheduler/log output.

Agent start:

```json
{
  "goal": "check the back door lock",
  "task": "nightly_door_lock_check",
  "skill": "check_door_locks",
  "targets": ["back_door"],
  "max_seconds": 300,
  "idle_seconds": 20
}
```

`goal` is required. The Pi records the mission and exposes it through status and
observe responses, but it does not interpret the mission when deciding whether
movement is safe.

Movement intent:

```json
{
  "type": "drive_pulse",
  "direction": "forward",
  "duration": 0.25,
  "speed": 35,
  "reason": "approach back door inspection spot"
}
```

Veto response:

```json
{
  "ok": true,
  "executed": false,
  "direction": "forward",
  "reflex": {
    "allowed": false,
    "reason": "center_blocked",
    "recommended_direction": "left"
  }
}
```

## Scheduled Inspection Model

Scheduled tasks should be represented as constrained skills, not open-ended
autonomy.

Example task definition:

```yaml
name: nightly_door_lock_check
schedule: "22:00"
skill: check_door_locks
targets:
  - back_door
  - garage_entry_door
report_to: phone
```

A skill should define:

- Known targets.
- Preferred inspection spots.
- Required evidence.
- Confidence threshold.
- Retry behavior.
- Failure wording.

This keeps scheduled jobs repeatable and makes the agent's reasoning auditable.

## Evidence and Reporting

Agent tasks should produce structured inspection results:

```json
{
  "task": "nightly_door_lock_check",
  "target": "back_door",
  "state": "locked",
  "confidence": 0.88,
  "evidence": [
    "wide image shows the back door",
    "lock crop shows deadbolt thumb turn in locked orientation"
  ],
  "image_refs": ["wide.jpg", "lock_crop.jpg"]
}
```

The robot should report uncertainty explicitly instead of guessing. For example:

```text
Back door lock is uncertain. I reached the inspection spot, but the image is too
dark to confirm the deadbolt state.
```

## Safety Invariants

- Agents never call motor driver methods.
- Agents never bypass `ValleController`.
- Movement is always a bounded pulse.
- `/stop` ends any active session immediately.
- Only one session can be active at a time.
- Agent movement requires fresh reflex input.
- Scheduled inspection reports should include confidence and evidence.

## Future Extensions

- Teach mode for recording routes and inspection spots.
- Persistent house map of rooms, landmarks, and target viewpoints.
- Evidence image storage with task run IDs.
- Mission scheduler service for recurring jobs.
- Dedicated vision inspector for lock state, stove knobs, floor blockers, and
  pet bowl status.
- Notification policy for all-clear, uncertain, and urgent findings.

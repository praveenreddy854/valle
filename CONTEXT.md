# Valle

Valle is a standalone robotic car context, separate from the Home Assistant voice-assistant application.

## Language

**Valle**:
A small robotic car that accepts remote movement requests.
_Avoid_: HA Voice Assistant device, Home Assistant device.

**Movement command**:
A short-lived request for Valle to move in one direction before returning to stopped.
_Avoid_: Latched command, continuous drive command.

**Speed setting**:
A relative movement-command modifier that controls how strongly Valle drives its motors.
_Avoid_: Velocity, acceleration.

**Siri control request**:
A same-network request initiated by a Siri Shortcut to control Valle.
_Avoid_: Home Assistant command, voice-assistant command.

**Stop command**:
A movement request that returns Valle to stopped immediately.
_Avoid_: Pause command, idle command.

**Stopped state**:
Valle's safe non-moving state.
_Avoid_: Idle mode, paused state.

**Pivot turn**:
A movement command where Valle rotates in place instead of following a forward arc.
_Avoid_: Steering turn, arc turn.

**Autopilot session**:
A bounded period during which Valle drives itself based on what its camera sees, without per-step user commands.
_Avoid_: Autonomous mode, self-driving session, exploration mode.

**Reflex driving**:
The behavior Valle exhibits inside an **Autopilot session**: drive whenever the forward path is clear, turn toward the clearer side when blocked, reverse when stuck. No memory of past frames, no map, no destination.
_Avoid_: Path planning, navigation, exploration, wandering.

## Relationships

- **Valle** is independent from the HA Voice Assistant smart-home context.
- A **Siri control request** may produce a **Movement command** or a **Stop command**.
- A **Movement command** is momentary rather than latched.
- A **Movement command** may include a **Speed setting**.
- A **Stop command** overrides any active **Movement command** and returns Valle to the **Stopped state**.
- Valle starts in the **Stopped state**.
- Left and right turn requests are **Pivot turn**s.
- An **Autopilot session** drives Valle independently of any **Movement command** or **Siri control request**.
- During an **Autopilot session**, **Siri control request**s are rejected — Valle answers only to the session.
- The behavior inside an **Autopilot session** is **Reflex driving**, not navigation toward a destination.
- An **Autopilot session** ends on an explicit **Stop command**, on a hard time cap, or when no forward or backward motion has been commanded for a no-progress window.

## Example dialogue

> **Dev:** "Should Valle be added as another Home Assistant-controlled device inside the existing voice assistant?"
> **Domain expert:** "No - **Valle** is its own robotic car context."

> **Dev:** "Are Siri requests part of the existing HA Voice Assistant voice flow?"
> **Domain expert:** "No - a **Siri control request** controls **Valle** directly."

> **Dev:** "If Siri says 'drive forward', should **Valle** keep going until another command arrives?"
> **Domain expert:** "No - a **Movement command** is short-lived, and a **Stop command** can end it immediately."

> **Dev:** "Does Siri have to specify a motor power every time?"
> **Domain expert:** "No - a **Movement command** can use the default speed or include a **Speed setting**."

> **Dev:** "What should happen when Valle's controller starts?"
> **Domain expert:** "**Valle** should begin in the **Stopped state**."

> **Dev:** "When Siri says 'turn left', should **Valle** steer through a wide arc?"
> **Domain expert:** "No - left and right turns are **Pivot turn**s."

> **Dev:** "While **Valle** is in an **Autopilot session**, can Siri still send a forward command?"
> **Domain expert:** "No - during an **Autopilot session**, **Siri control request**s are rejected. Only the session drives Valle."

> **Dev:** "Does **Reflex driving** mean Valle remembers where it's been?"
> **Domain expert:** "No - **Reflex driving** has no memory. Each decision is made from the current camera frame alone."

> **Dev:** "Does an **Autopilot session** have a destination?"
> **Domain expert:** "No - it has no goal beyond 'don't be stopped.' If you want destination-following, that's a different concept than **Reflex driving**."

## Flagged ambiguities

- "Valle" could have meant a feature inside the HA Voice Assistant app - resolved: **Valle** is a standalone robotic car context.
- "drive forward" could have meant a latched motor state - resolved: **Movement command** means short-lived movement.
- "turn" could have meant either steering through an arc or rotating in place - resolved: turn requests are **Pivot turn**s.
- "auto-correct the drive" could have meant per-command steering correction during a **Movement command** - resolved: autocorrection lives only inside an **Autopilot session** as **Reflex driving**; a **Movement command** is never modified by perception.
- "detect objects" could have meant per-class object recognition for reflex - resolved: **Reflex driving** uses class-agnostic free-space sensing; per-class object recognition is a future, separate concern from reflex.

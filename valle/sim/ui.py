"""Browser UI for the digital twin: camera view, top-down map, controls."""
from __future__ import annotations

SIM_UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Valle digital twin</title>
<style>
  :root {
    --bg: #14171c; --panel: #1d2229; --line: #313842;
    --text: #e8e8e3; --muted: #9aa3ad; --accent: #f0b429;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; background: var(--bg); color: var(--text);
    font: 14px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  h1 { font-size: 18px; margin: 0 0 12px; }
  h1 span { color: var(--muted); font-weight: normal; font-size: 13px; }
  .panels { display: flex; gap: 16px; flex-wrap: wrap; }
  section {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: 12px; flex: 1 1 480px; min-width: 360px;
  }
  h2 { font-size: 13px; color: var(--muted); margin: 0 0 8px; text-transform: uppercase; letter-spacing: 0.06em; }
  img#cam { width: 100%; border-radius: 6px; display: block; background: #000; aspect-ratio: 4/3; }
  canvas#map { width: 100%; border-radius: 6px; background: #10131a; display: block; }
  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
  button {
    background: #2a313b; color: var(--text); border: 1px solid var(--line);
    border-radius: 8px; padding: 8px 14px; font-size: 14px; cursor: pointer;
  }
  button:hover { background: #353e4a; }
  button.warn { border-color: #7a4a1d; color: var(--accent); }
  #readout { color: var(--muted); font-family: ui-monospace, Menlo, monospace; font-size: 12px; margin-top: 10px; white-space: pre-wrap; }
  .hint { color: var(--muted); font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
<h1>Valle digital twin <span>— visual test</span></h1>
<div class="panels">
  <section>
    <h2>Robot camera</h2>
    <img id="cam" alt="simulated camera stream">
  </section>
  <section>
    <h2>Room map (top-down)</h2>
    <canvas id="map" width="640" height="560"></canvas>
  </section>
</div>
<div class="controls">
  <button data-cmd="forward">&#8593; Forward</button>
  <button data-cmd="backward">&#8595; Backward</button>
  <button data-cmd="left">&#8634; Left</button>
  <button data-cmd="right">&#8635; Right</button>
  <button id="stop" class="warn">&#9632; Stop</button>
  <button id="door">Toggle door lock</button>
  <button id="reset">Reset world</button>
</div>
<div id="readout">loading…</div>
<div class="hint">Keyboard: arrow keys or WASD to drive, space to stop. Driving is
rejected with 409 while an autopilot/agent session owns the robot — use the map
to watch the session drive instead.</div>
<script>
const CAMERA_PORT = {{ camera_port }};
document.getElementById("cam").src =
  `http://${location.hostname}:${CAMERA_PORT}/stream.mjpg`;

const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
let world = null, lastState = null, trail = [];

const WALL_COLORS = { wall: "#98a2ad", box: "#5b636e", door: "#8a5a28" };

fetch("/sim/world").then(r => r.json()).then(w => { world = w; });

function transform() {
  const pad = 30;
  const sx = (canvas.width - 2 * pad) / (world.bounds.max_x - world.bounds.min_x);
  const sy = (canvas.height - 2 * pad) / (world.bounds.max_y - world.bounds.min_y);
  const s = Math.min(sx, sy);
  return p => [
    pad + (p[0] - world.bounds.min_x) * s,
    canvas.height - pad - (p[1] - world.bounds.min_y) * s,
  ];
}

function draw() {
  if (!world || !lastState) return;
  const t = transform();
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  for (const wall of world.walls) {
    const [x0, y0] = t([wall.ax, wall.ay]);
    const [x1, y1] = t([wall.bx, wall.by]);
    ctx.strokeStyle = wall.kind === "door"
      ? (lastState.door && lastState.door.locked ? "#f0b429" : "#4cc38a")
      : (WALL_COLORS[wall.kind] || WALL_COLORS.wall);
    ctx.lineWidth = wall.kind === "door" ? 7 : 4;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    if (wall.kind === "door") {
      ctx.fillStyle = ctx.strokeStyle;
      ctx.font = "12px sans-serif";
      const label = lastState.door.locked ? "door · locked" : "door · unlocked";
      ctx.fillText(label, (x0 + x1) / 2 - 34, Math.min(y0, y1) + 18);
    }
  }

  for (const obj of world.objects) {
    const [x, y] = t([obj.x, obj.y]);
    ctx.fillStyle = obj.color;
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#9aa3ad"; ctx.font = "12px sans-serif";
    ctx.fillText(obj.name, x + 10, y + 4);
  }

  if (trail.length > 1) {
    ctx.strokeStyle = "rgba(240, 180, 41, 0.45)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    trail.forEach((p, i) => {
      const [x, y] = t(p);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  const robot = lastState.robot;
  const [rx, ry] = t([robot.x, robot.y]);
  const heading = robot.heading_deg * Math.PI / 180;
  ctx.fillStyle = "#f0b429";
  ctx.beginPath(); ctx.arc(rx, ry, 9, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "#14171c"; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(rx, ry);
  ctx.lineTo(rx + 16 * Math.cos(heading), ry - 16 * Math.sin(heading));
  ctx.stroke();
}

async function poll() {
  try {
    const state = await (await fetch("/sim/state")).json();
    lastState = state;
    const last = trail[trail.length - 1];
    if (!last || Math.hypot(last[0] - state.robot.x, last[1] - state.robot.y) > 0.02) {
      trail.push([state.robot.x, state.robot.y]);
      if (trail.length > 800) trail.shift();
    }
    const door = state.door ? (state.door.locked ? "locked" : "unlocked") : "n/a";
    document.getElementById("readout").textContent =
      `pose x=${state.robot.x.toFixed(2)}m y=${state.robot.y.toFixed(2)}m ` +
      `heading=${state.robot.heading_deg.toFixed(0)}°   ` +
      `driver=${state.driver.action}@${(state.driver.speed * 100).toFixed(0)}%   ` +
      `door=${door} (${state.door ? state.door.distance.toFixed(2) : "?"}m)   ` +
      state.objects.map(o => `${o.name}=${o.distance.toFixed(2)}m@${o.bearing_deg.toFixed(0)}°`).join(" ");
    draw();
  } catch (e) { /* sim restarting; keep polling */ }
}
setInterval(poll, 200);
poll();

async function drive(cmd) {
  await fetch(`/${cmd}?duration=0.5&speed=70`, { method: "POST" });
}
document.querySelectorAll("button[data-cmd]").forEach(b =>
  b.addEventListener("click", () => drive(b.dataset.cmd)));
document.getElementById("stop").addEventListener("click",
  () => fetch("/stop", { method: "POST" }));
document.getElementById("door").addEventListener("click", async () => {
  if (!lastState || !lastState.door) return;
  await fetch("/sim/door", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ locked: !lastState.door.locked }),
  });
  poll();
});
document.getElementById("reset").addEventListener("click", async () => {
  await fetch("/sim/reset", { method: "POST" });
  trail = [];
  poll();
});

const KEYS = {
  ArrowUp: "forward", ArrowDown: "backward", ArrowLeft: "left", ArrowRight: "right",
  w: "forward", s: "backward", a: "left", d: "right",
};
document.addEventListener("keydown", event => {
  if (event.repeat) return;
  if (event.key === " ") { fetch("/stop", { method: "POST" }); event.preventDefault(); return; }
  const cmd = KEYS[event.key];
  if (cmd) { drive(cmd); event.preventDefault(); }
});
</script>
</body>
</html>
"""

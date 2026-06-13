from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, Response, jsonify, render_template_string, request


def create_portal_blueprint() -> Blueprint:
    portal = Blueprint("portal", __name__)

    @portal.get("/portal")
    def index() -> str:
        return render_template_string(_PORTAL_HTML)

    @portal.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status=204)

    @portal.get("/portal/api/status")
    def status() -> Any:
        return jsonify(
            {
                "ok": True,
                "camera_url": _camera_url(),
                "otel_exporter_otlp_endpoint": _masked_url(
                    os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
                ),
                "log_dir": str(_log_dir()),
            }
        )

    @portal.get("/portal/api/files")
    def files() -> Any:
        log_dir = _log_dir()
        return jsonify(
            {
                "ok": True,
                "files": [
                    _file_summary(path)
                    for path in sorted(log_dir.glob("*"))
                    if _is_portal_file(path)
                ],
            }
        )

    @portal.get("/portal/api/files/<path:name>")
    def file_tail(name: str) -> Any:
        try:
            path = _safe_portal_file(name)
            lines = _tail_lines(path, _tail_count())
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except FileNotFoundError:
            return jsonify({"ok": False, "error": "file not found"}), 404

        return jsonify(
            {
                "ok": True,
                "file": _file_summary(path),
                "lines": [_parse_line(line) for line in lines],
            }
        )

    @portal.get("/portal/camera.mjpg")
    def camera_proxy() -> Response:
        camera_url = _camera_url()
        try:
            import requests
        except ImportError:
            return Response("requests is not installed", status=503)

        try:
            upstream = requests.get(camera_url, stream=True, timeout=(3, 10))
            upstream.raise_for_status()
        except requests.RequestException as exc:
            return Response(f"camera feed unavailable: {exc}", status=503)

        content_type = upstream.headers.get(
            "content-type", "multipart/x-mixed-replace; boundary=FRAME"
        )

        def generate() -> Any:
            try:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return Response(generate(), content_type=content_type)

    return portal


def _log_dir() -> Path:
    return Path(os.getenv("VALLE_LOG_DIR", "logs"))


def _camera_url() -> str:
    return os.getenv(
        "VALLE_PORTAL_CAMERA_URL",
        os.getenv("VALLE_CAMERA_URL", "http://rpi.local:8081/stream.mjpg"),
    )


def _tail_count() -> int:
    raw = request.args.get("tail", "200")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("tail must be an integer") from exc
    return min(max(value, 1), 2000)


def _is_portal_file(path: Path) -> bool:
    return path.is_file() and (
        path.name.endswith(".log") or path.name.endswith(".traces.jsonl")
    )


def _safe_portal_file(name: str) -> Path:
    if Path(name).name != name:
        raise ValueError("file name must not include path separators")
    path = _log_dir() / name
    if not _is_portal_file(path):
        raise FileNotFoundError(name)
    return path


def _tail_lines(path: Path, count: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return list(deque(handle, maxlen=count))


def _parse_line(line: str) -> dict[str, Any]:
    raw = line.rstrip("\n")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _file_summary(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "kind": "traces" if path.name.endswith(".traces.jsonl") else "logs",
        "size_bytes": stat.st_size,
        "updated_at": stat.st_mtime,
    }


def _masked_url(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(value)
    if not parts.netloc:
        return value
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{hostname}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


_PORTAL_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Valle Portal</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f5f7f8;
      --panel: #ffffff;
      --text: #1d252b;
      --muted: #5f6f78;
      --line: #d7dee2;
      --accent: #1f7a65;
      --accent-strong: #145c4b;
      --danger: #9f2d20;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #111518;
        --panel: #191f23;
        --text: #eef2f3;
        --muted: #9aa8ae;
        --line: #303a40;
        --accent: #4db69c;
        --accent-strong: #78d0bd;
        --danger: #ff786b;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 650;
    }
    button, select {
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 0 10px;
      font: inherit;
    }
    button {
      cursor: pointer;
      color: white;
      background: var(--accent);
      border-color: var(--accent);
      font-weight: 600;
    }
    button:hover { background: var(--accent-strong); }
    main {
      display: grid;
      grid-template-columns: minmax(340px, 42%) minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      min-height: calc(100vh - 67px);
    }
    section {
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    .title {
      margin: 0;
      font-size: 15px;
      font-weight: 650;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .camera {
      display: block;
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: contain;
      background: #050708;
    }
    .status-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      padding: 12px 14px;
      border-top: 1px solid var(--line);
    }
    .status-item {
      min-width: 0;
      font-size: 12px;
      color: var(--muted);
    }
    .status-value {
      display: block;
      margin-top: 3px;
      overflow-wrap: anywhere;
      color: var(--text);
      font-family: var(--mono);
      font-size: 12px;
    }
    .log-tools {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }
    #fileSelect {
      min-width: min(100%, 280px);
      max-width: 100%;
    }
    .log-lines {
      height: calc(100vh - 152px);
      overflow: auto;
      margin: 0;
      padding: 12px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .empty, .error {
      color: var(--muted);
      padding: 12px 14px;
      font-size: 13px;
    }
    .error { color: var(--danger); }
    @media (max-width: 900px) {
      header { align-items: flex-start; flex-direction: column; }
      main { grid-template-columns: 1fr; }
      .log-lines { height: 48vh; }
      .status-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Valle Portal</h1>
    <div class="meta" id="updated">Loading...</div>
  </header>
  <main>
    <section aria-label="Camera feed">
      <div class="section-head">
        <h2 class="title">Camera</h2>
        <button id="reloadCamera" type="button" title="Reconnect camera feed">Reconnect</button>
      </div>
      <img class="camera" id="camera" alt="Valle camera feed" src="/portal/camera.mjpg">
      <div class="status-grid">
        <div class="status-item">Camera source <span class="status-value" id="cameraUrl">-</span></div>
        <div class="status-item">OTLP endpoint <span class="status-value" id="otelEndpoint">unset</span></div>
        <div class="status-item">Log directory <span class="status-value" id="logDir">-</span></div>
      </div>
    </section>
    <section aria-label="Logs and traces">
      <div class="section-head">
        <h2 class="title">Logs and Traces</h2>
        <div class="log-tools">
          <select id="fileSelect" aria-label="Log or trace file"></select>
          <select id="tailSelect" aria-label="Tail line count">
            <option value="100">100 lines</option>
            <option value="200" selected>200 lines</option>
            <option value="500">500 lines</option>
            <option value="1000">1000 lines</option>
          </select>
          <button id="refreshLogs" type="button" title="Refresh logs">Refresh</button>
        </div>
      </div>
      <pre class="log-lines" id="logLines">Loading...</pre>
    </section>
  </main>
  <script>
    const fileSelect = document.querySelector("#fileSelect");
    const tailSelect = document.querySelector("#tailSelect");
    const logLines = document.querySelector("#logLines");
    const updated = document.querySelector("#updated");

    function formatTime(seconds) {
      return new Date(seconds * 1000).toLocaleString();
    }

    function renderLine(line) {
      if (line.message) {
        const stamp = line.timestamp || "";
        const level = line.level || "";
        const logger = line.logger || "";
        const trace = line.trace_id ? ` trace=${line.trace_id}` : "";
        return `${stamp} ${level} ${logger}${trace}\\n${line.message}`;
      }
      if (line.name && line.context) {
        const status = line.status?.status_code || "";
        const trace = line.context?.trace_id || "";
        return `${line.name} ${status} trace=${trace}\\n${JSON.stringify(line.attributes || {})}`;
      }
      return JSON.stringify(line);
    }

    async function loadStatus() {
      const response = await fetch("/portal/api/status");
      const payload = await response.json();
      document.querySelector("#cameraUrl").textContent = payload.camera_url || "-";
      document.querySelector("#otelEndpoint").textContent =
        payload.otel_exporter_otlp_endpoint || "unset";
      document.querySelector("#logDir").textContent = payload.log_dir || "-";
    }

    async function loadFiles() {
      const selected = fileSelect.value;
      const response = await fetch("/portal/api/files");
      const payload = await response.json();
      fileSelect.replaceChildren();
      for (const file of payload.files || []) {
        const option = document.createElement("option");
        option.value = file.name;
        option.textContent = `${file.name} (${file.kind}, ${formatTime(file.updated_at)})`;
        fileSelect.appendChild(option);
      }
      if (selected) fileSelect.value = selected;
      if (!fileSelect.value && fileSelect.options.length > 0) {
        fileSelect.selectedIndex = 0;
      }
      await loadTail();
    }

    async function loadTail() {
      if (!fileSelect.value) {
        logLines.textContent = "No log or trace files found yet.";
        return;
      }
      const tail = encodeURIComponent(tailSelect.value);
      const name = encodeURIComponent(fileSelect.value);
      const response = await fetch(`/portal/api/files/${name}?tail=${tail}`);
      const payload = await response.json();
      if (!payload.ok) {
        logLines.textContent = payload.error || "Unable to load file.";
        return;
      }
      logLines.textContent = (payload.lines || []).map(renderLine).join("\\n\\n");
      updated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    }

    document.querySelector("#refreshLogs").addEventListener("click", loadFiles);
    fileSelect.addEventListener("change", loadTail);
    tailSelect.addEventListener("change", loadTail);
    document.querySelector("#reloadCamera").addEventListener("click", () => {
      document.querySelector("#camera").src = `/portal/camera.mjpg?t=${Date.now()}`;
    });

    loadStatus().catch((error) => {
      document.querySelector("#otelEndpoint").textContent = `status error: ${error}`;
    });
    loadFiles().catch((error) => {
      logLines.textContent = `portal error: ${error}`;
    });
    setInterval(loadTail, 5000);
  </script>
</body>
</html>
"""

"""Mac-side autopilot brain for Valle.

Pulls MJPEG frames from the Pi, runs monocular depth, reduces to
three vertical clearance strips, and drives the Pi via the
``/autopilot`` session API. Reflex driving only - no map, no memory.
"""

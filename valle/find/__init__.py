"""Off-device object-find service for Valle.

Dials a WebSocket into the Pi (``/brain/find``), pulls camera frames
from the same MJPEG stream the autopilot brain uses, and answers
text-queried object lookups on demand with an open-vocabulary
detector (OWLv2).
"""

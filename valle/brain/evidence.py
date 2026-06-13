"""Evidence image storage for agent mission runs.

Each run gets its own directory under the evidence root; frames are saved
as numbered JPEGs so inspection results can reference real images.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class EvidenceStore:
    def __init__(self, root: Path | str, run_id: str) -> None:
        self._dir = Path(root) / run_id
        self._run_id = run_id
        self._items: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls, run_id: str) -> "EvidenceStore":
        return cls(os.getenv("VALLE_EVIDENCE_DIR", "evidence"), run_id)

    @property
    def run_id(self) -> str:
        return self._run_id

    def save(self, image: Any, label: str) -> dict[str, Any]:
        import cv2

        self._dir.mkdir(parents=True, exist_ok=True)
        filename = f"{len(self._items) + 1:02d}-{_slug(label)}.jpg"
        path = self._dir / filename
        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"failed to write evidence image {path}")
        item = {
            "file": filename,
            "label": label,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._items.append(item)
        return {**item, "path": str(path)}

    def items(self) -> list[dict[str, Any]]:
        return list(self._items)


def evidence_root() -> Path:
    return Path(os.getenv("VALLE_EVIDENCE_DIR", "evidence"))


def _slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug or "frame"

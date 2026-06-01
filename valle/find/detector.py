from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np


log = logging.getLogger("valle.find.detector")


class Detector:
    """OWLv2 zero-shot detector wrapper."""

    def __init__(
        self,
        model_id: str,
        device: str = "auto",
        *,
        score_threshold: float = 0.1,
        max_results: int = 5,
    ) -> None:
        self._model_id = model_id
        self._device = _resolve_device(device)
        self._score_threshold = score_threshold
        self._max_results = max_results
        self._pipeline: Any = None

    def load(self) -> None:
        if self._pipeline is not None:
            return
        try:
            import torch  # noqa: F401
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError(
                "transformers and torch are required. Install with: "
                "pip install valle[brain]"
            ) from exc
        log.info("loading detector %s on %s", self._model_id, self._device)
        self._pipeline = pipeline(
            "zero-shot-object-detection",
            model=self._model_id,
            device=self._device,
        )

    def detect(
        self, image_bgr: np.ndarray, query: str
    ) -> list[dict[str, Any]]:
        if self._pipeline is None:
            self.load()
        from PIL import Image

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        results = self._pipeline(pil, candidate_labels=[query])
        kept = [
            {
                "score": round(float(r["score"]), 3),
                "label": r["label"],
                "box": {k: int(v) for k, v in r["box"].items()},
            }
            for r in results
            if float(r["score"]) >= self._score_threshold
        ]
        kept.sort(key=lambda r: r["score"], reverse=True)
        return kept[: self._max_results]


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"

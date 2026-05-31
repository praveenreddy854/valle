from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np


if TYPE_CHECKING:
    pass


log = logging.getLogger("valle.brain.depth")


class DepthEstimator:
    """Monocular relative-depth estimator wrapping Depth Anything V2 Small.

    Returns a per-pixel relative-depth map normalised to ``[0, 1]``,
    where higher values mean closer.
    """

    def __init__(self, model_id: str, device: str = "auto") -> None:
        self._model_id = model_id
        self._device = _resolve_device(device)
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
        log.info("loading depth model %s on %s", self._model_id, self._device)
        self._pipeline = pipeline(
            "depth-estimation", model=self._model_id, device=self._device
        )

    def infer(self, image_bgr: np.ndarray) -> np.ndarray:
        if self._pipeline is None:
            self.load()
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        from PIL import Image  # cheap, already an opencv dep transitively

        pil = Image.fromarray(rgb)
        result = self._pipeline(pil)
        predicted = result["predicted_depth"]
        depth = predicted.detach().cpu().numpy()
        if depth.ndim == 3:
            depth = depth[0]
        if depth.shape != image_bgr.shape[:2]:
            depth = cv2.resize(
                depth,
                (image_bgr.shape[1], image_bgr.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        return _normalise(depth)


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


def _normalise(depth: np.ndarray) -> np.ndarray:
    depth = depth.astype(np.float32)
    lo = float(np.nanmin(depth))
    hi = float(np.nanmax(depth))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-6:
        return np.zeros_like(depth, dtype=np.float32)
    return (depth - lo) / (hi - lo)

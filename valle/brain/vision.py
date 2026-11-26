"""Vision-language scene inspection over Azure OpenAI or Anthropic Claude.

Answers state questions an open-vocabulary detector cannot, e.g. "is the
deadbolt engaged?" or "is the door ajar?". Returns structured JSON.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

_SYSTEM = (
    "You inspect a single photo from a small home robot's low camera. Answer the "
    "question strictly from what is visible. Reply with JSON only: "
    '{"answer": string, "confidence": number 0-1, "observations": [string]}. '
    "If the view is insufficient, say so in answer and use low confidence."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
        "observations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "confidence", "observations"],
    "additionalProperties": False,
}


class SceneInspector:
    def __init__(self, client: Any, model: str, provider: str = "azure") -> None:
        self._client = client
        self._model = model
        self._provider = provider

    @classmethod
    def from_config(cls, config: Any) -> "SceneInspector":
        if config.llm_provider == "anthropic":
            import anthropic

            model = os.getenv("VALLE_VISION_MODEL") or config.anthropic_model
            client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            return cls(client, model, provider="anthropic")

        from openai import AzureOpenAI, OpenAI

        model = os.getenv("VALLE_VISION_DEPLOYMENT") or config.azure_deployment
        if "/openai/v1" in config.azure_endpoint:
            client = OpenAI(api_key=config.azure_api_key, base_url=config.azure_endpoint)
        else:
            base = config.azure_endpoint.split("/openai/")[0]
            client = AzureOpenAI(
                api_key=config.azure_api_key,
                azure_endpoint=base,
                api_version=config.azure_api_version,
            )
        return cls(client, model)

    def inspect(self, image_bgr: Any, question: str) -> dict[str, Any]:
        import cv2

        ok, jpeg = cv2.imencode(".jpg", image_bgr)
        if not ok:
            raise RuntimeError("could not encode camera frame")
        b64 = base64.b64encode(jpeg.tobytes()).decode()
        if self._provider == "anthropic":
            text = self._inspect_anthropic(b64, question)
        else:
            text = self._inspect_openai(b64, question)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"vision model returned non-JSON: {text[:200]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("vision model returned non-object JSON")
        return parsed

    def _inspect_anthropic(self, b64: str, question: str) -> str:
        # Server-side fallback re-runs a refused request on another Claude model.
        response = self._client.beta.messages.create(
            model=self._model,
            max_tokens=16000,
            system=_SYSTEM,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={
                "fallbacks": "default",
                "output_config": {"format": {"type": "json_schema", "schema": _SCHEMA}},
            },
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                        },
                        {"type": "text", "text": question},
                    ],
                }
            ],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("vision model refused the request")
        return next((b.text for b in response.content if b.type == "text"), "")

    def _inspect_openai(self, b64: str, question: str) -> str:
        data_url = "data:image/jpeg;base64," + b64
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
        return response.choices[0].message.content or ""

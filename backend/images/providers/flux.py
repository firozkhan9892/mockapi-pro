"""Flux provider.

Connects to a Flux-compatible image API. The exact shape of the upstream
API is configurable via environment variables (see ``ProviderConfig``):

* ``FLUX_API_URL``  – base URL of the provider (required).
* ``FLUX_API_KEY``  – bearer token (optional, many local setups need none).
* ``FLUX_GENERATE_PATH``  – endpoint accepting a JSON prompt body
  (default ``/v1/generate``).
* ``FLUX_STATUS_PATH``    – ``{id}``-templated job status endpoint
  (default ``/v1/status/{id}``).
* ``FLUX_IMAGE_PATH``     – ``{id}``-templated image bytes endpoint
  (default ``/v1/image/{id}``).
* ``FLUX_TIMEOUT``        – total seconds to wait for a job (default 60).
* ``FLUX_POLL_INTERVAL``  – seconds between status polls (default 2).

Supported response shapes (either is accepted, so the same provider works
against a local server, ComfyUI-style wrapper, RunPod, Replicate-style
endpoint, or an OpenAI-compatible /v1/images/generations variant that this
configuration can mirror):

1. Synchronous: ``200`` JSON with ``"image": "<base64>"`` (optionally a
   ``data:image/png;base64,`` prefix), or raw PNG bytes in the body.
2. Asynchronous: ``202`` JSON with ``"id": "<job_id>"``; then poll the
   status endpoint until ``"status"`` is ``"completed"``/``succeeded`` or
   ``"failed"``/``error``/``ERROR``, and finally fetch the image bytes.
"""

from __future__ import annotations

import base64
import json
import logging
import time

import requests

from .base import BaseProvider, GenerationStatus, ProviderResult
from .config import ProviderConfig, auth_headers

logger = logging.getLogger(__name__)


class FluxProvider(BaseProvider):
    name = "flux"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig.from_env("FLUX")

    @property
    def configured(self) -> bool:
        return self.config.configured

    def generate(self, prompt, negative_prompt, width, height, model):
        if not self.configured:
            return ProviderResult(
                GenerationStatus.FAILED,
                error="Flux provider is not configured (set FLUX_API_URL)",
            )

        try:
            payload = {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "model": model,
            }
            headers = {"Content-Type": "application/json", **auth_headers(self.config.api_key)}
            url = self.config.api_url.rstrip("/") + self.config.generate_path
            logger.debug("FluxProvider: POST %s", url)
            resp = requests.post(url, json=payload, headers=headers, timeout=self.config.timeout)

            if resp.status_code in (200, 201):
                # Synchronous completion: base64 field or raw bytes.
                if resp.headers.get("Content-Type", "").startswith("application/json"):
                    data = resp.json()
                    image_bytes = _extract_image(data)
                    if image_bytes is None:
                        remote_id = _remote_id(data)
                        if remote_id:
                            return self._poll(remote_id)
                        return ProviderResult(
                            GenerationStatus.FAILED,
                            error="Flux provider returned JSON without an image",
                        )
                    return ProviderResult(GenerationStatus.COMPLETED, image_bytes=image_bytes)

                content = _as_png(resp.content)
                if content is not None:
                    return ProviderResult(GenerationStatus.COMPLETED, image_bytes=content)
                return ProviderResult(
                    GenerationStatus.FAILED,
                    error="Flux provider returned an unsupported response body",
                )

            if resp.status_code == 202:
                data = _safe_json(resp)
                remote_id = _remote_id(data)
                if not remote_id:
                    return ProviderResult(
                        GenerationStatus.FAILED,
                        error="Flux provider returned 202 without a job id",
                    )
                return self._poll(remote_id)

            return ProviderResult(
                GenerationStatus.FAILED,
                error=f"Flux provider HTTP {resp.status_code}: {resp.text[:200]}",
            )

        except requests.RequestException as exc:
            return ProviderResult(GenerationStatus.FAILED, error=f"Flux provider request failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("FluxProvider unexpected error")
            return ProviderResult(GenerationStatus.FAILED, error=f"Flux provider error: {exc}")

    def _poll(self, remote_id: str) -> ProviderResult:
        url = self.config.api_url.rstrip("/") + self.config.status_path.format(id=remote_id)
        headers = auth_headers(self.config.api_key)
        deadline = time.time() + self.config.timeout
        while time.time() < deadline:
            try:
                resp = requests.get(url, headers=headers, timeout=max(5, self.config.timeout))
            except requests.RequestException as exc:
                return ProviderResult(GenerationStatus.FAILED, error=f"Flux status request failed: {exc}")
            if resp.status_code != 200:
                return ProviderResult(
                    GenerationStatus.FAILED,
                    error=f"Flux status HTTP {resp.status_code}: {resp.text[:200]}",
                )
            data = _safe_json(resp)
            status = _remote_status(data)
            if status in ("completed", "succeeded"):
                image_bytes = _extract_image(data)
                if image_bytes is None:
                    image_bytes = self._fetch_image(remote_id)
                if isinstance(image_bytes, ProviderResult):
                    return image_bytes
                return ProviderResult(GenerationStatus.COMPLETED, image_bytes=image_bytes)
            if status in ("failed", "error", "ERROR"):
                return ProviderResult(
                    GenerationStatus.FAILED,
                    error=f"Flux job failed: {data.get('error', 'unknown') if isinstance(data, dict) else ''}",
                )
            time.sleep(self.config.poll_interval)

        return ProviderResult(
            GenerationStatus.FAILED,
            error="Flux provider timed out waiting for image generation",
        )

    def _fetch_image(self, remote_id: str) -> bytes | ProviderResult:
        url = self.config.api_url.rstrip("/") + self.config.image_path.format(id=remote_id)
        headers = auth_headers(self.config.api_key)
        try:
            resp = requests.get(url, headers=headers, timeout=self.config.timeout)
        except requests.RequestException as exc:
            return ProviderResult(GenerationStatus.FAILED, error=f"Flux image fetch failed: {exc}")
        if resp.status_code != 200:
            return ProviderResult(
                GenerationStatus.FAILED,
                error=f"Flux image fetch HTTP {resp.status_code}: {resp.text[:200]}",
            )
        content = _as_png(resp.content)
        if content is None:
            return ProviderResult(
                GenerationStatus.FAILED,
                error="Flux image endpoint returned a non-PNG body",
            )
        return content


def _safe_json(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _remote_id(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("id", "job_id", "request_id", "operation"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _remote_status(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    return str(data.get("status", data.get("state", ""))).lower()


def _extract_image(data: dict) -> bytes | None:
    if not isinstance(data, dict):
        return None
    for key in ("image", "images", "base64", "b64", "output"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return _decode_b64(value)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return _decode_b64(first)
            if isinstance(first, dict):
                return _decode_b64(first.get("b64", first.get("image", "")))
    return None


def _decode_b64(value: str) -> bytes | None:
    raw = value.strip()
    if raw.startswith("data:"):
        marker = raw.find(";base64,")
        if marker != -1:
            raw = raw[marker + len(";base64,"):]
    try:
        return base64.b64decode(raw, validate=False)
    except Exception:
        return None


def _as_png(content: bytes) -> bytes | None:
    """Return raw bytes if they look like a PNG, else None."""
    if not content:
        return None
    return content if content[:8] == b"\x89PNG\r\n\x1a\n" else None

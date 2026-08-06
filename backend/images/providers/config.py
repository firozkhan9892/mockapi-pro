"""Provider configuration loaded from environment variables / .env files.

Nothing is hardcoded. Each provider reads its settings from here so the
module can be pointed at a local Flux server, ComfyUI, RunPod, Replicate,
or any OpenAI-compatible image API at deploy time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    _env_file = Path(__file__).resolve().parents[2] / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except Exception:
    # dotenv is optional; environment variables are also read directly from
    # the process environment, so a missing/unparseable .env is non-fatal.
    pass


def _strip(value: str | None) -> str:
    return (value or "").strip()


def _int(value: str | None, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ProviderConfig:
    api_url: str = ""
    api_key: str = ""
    timeout: int = 60
    poll_interval: float = 2.0
    # Endpoint paths. ``{id}`` is replaced with the remote job id when present.
    generate_path: str = "/v1/generate"
    status_path: str = "/v1/status/{id}"
    image_path: str = "/v1/image/{id}"

    @classmethod
    def from_env(cls, prefix: str = "FLUX") -> "ProviderConfig":
        env = os.environ
        return cls(
            api_url=_strip(env.get(f"{prefix}_API_URL")),
            api_key=_strip(env.get(f"{prefix}_API_KEY")),
            timeout=_int(env.get(f"{prefix}_TIMEOUT"), 60),
            poll_interval=max(
                0.1,
                _int(env.get(f"{prefix}_POLL_INTERVAL"), 2),
            ),
            generate_path=_strip(env.get(f"{prefix}_GENERATE_PATH") or "/v1/generate"),
            status_path=_strip(env.get(f"{prefix}_STATUS_PATH") or "/v1/status/{id}"),
            image_path=_strip(env.get(f"{prefix}_IMAGE_PATH") or "/v1/image/{id}"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_url)


def auth_headers(api_key: str) -> dict:
    """Return request headers carrying an Authorization bearer token when present."""
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


__all__ = ["ProviderConfig", "auth_headers"]

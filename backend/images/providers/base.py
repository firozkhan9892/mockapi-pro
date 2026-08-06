"""Base abstractions for image-generation providers.

A provider turns a text prompt into image bytes. It may either return the
bytes synchronously (status ``completed``) or delegate to an asynchronous
remote job that must be polled (status ``processing`` until resolved). When
credentials are missing the provider is simply "not configured" — callers can
fall back to a dev-mode placeholder without crashing.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Optional


class GenerationStatus(str, enum.Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclasses.dataclass
class ProviderResult:
    """Outcome of a provider.generate() call."""

    status: GenerationStatus
    image_bytes: Optional[bytes] = None
    remote_id: Optional[str] = None
    error: Optional[str] = None


class BaseProvider:
    """Interface every image provider implements.

    ``generate`` MUST be synchronous from the caller's perspective: it blocks
    (polling the remote API when needed) until the image is ready, failed, or
    the configured timeout elapses. The route handler therefore always gets a
    terminal result and can persist a single status to the database.
    """

    name: str = "base"

    def generate(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        model: str,
    ) -> ProviderResult:
        raise NotImplementedError

    @property
    def configured(self) -> bool:
        return True

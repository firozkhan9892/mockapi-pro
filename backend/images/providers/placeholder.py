"""Development fallback provider.

Used automatically when no real provider is configured (i.e. when
``FLUX_API_URL`` is unset) so the full request lifecycle — auth, database,
storage, API — remains exercisable in tests and local runs without a live
AI backend. It produces a deterministic placeholder PNG seeded by the prompt.
"""

from __future__ import annotations

from .. import utils
from .base import BaseProvider, GenerationStatus, ProviderResult


class PlaceholderProvider(BaseProvider):
    name = "placeholder"

    def generate(self, prompt, negative_prompt, width, height, model):
        try:
            png = utils.make_placeholder_png(width, height, prompt)
            return ProviderResult(GenerationStatus.COMPLETED, image_bytes=png)
        except Exception as exc:  # pragma: no cover - defensive
            return ProviderResult(GenerationStatus.FAILED, error=f"Placeholder generation failed: {exc}")

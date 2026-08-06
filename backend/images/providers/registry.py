"""Provider registry: maps a model name to an image provider.

Extend by adding entries to ``REAL_PROVIDERS`` (e.g. ComfyUIProvider for
``stable-diffusion-xl``) and/or adding env-driven config in ``config.py``.
"""

from __future__ import annotations

from .base import BaseProvider, GenerationStatus, ProviderResult
from .config import ProviderConfig
from .flux import FluxProvider
from .placeholder import PlaceholderProvider

# Models backed by a real, env-configured provider. Add new providers here.
REAL_PROVIDERS = {
    "flux-schnell": lambda: FluxProvider(ProviderConfig.from_env("FLUX")),
    "kontext-flux": lambda: FluxProvider(ProviderConfig.from_env("KONTEXT")),
}

_FALLBACK = PlaceholderProvider()


def get_provider(model: str) -> BaseProvider:
    """Return a provider for ``model``.

    Real providers are only used when their env-config is present; otherwise a
    dev-mode placeholder provider is returned so requests never 500 purely for
    a missing API key.
    """
    factory = REAL_PROVIDERS.get(model)
    if factory is not None:
        provider = factory()
        if provider.configured:
            return provider
    return _FALLBACK


__all__ = ["BaseProvider", "GenerationStatus", "ProviderResult", "FluxProvider",
           "PlaceholderProvider", "ProviderConfig", "get_provider", "REAL_PROVIDERS"]

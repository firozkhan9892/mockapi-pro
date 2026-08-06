"""Image-generation provider abstractions and implementations.

Providers turn a prompt into image bytes. They are pluggable: each is configured
from environment variables via :mod:`images.providers.config`, and the active
provider for a given model is resolved by :func:`images.providers.registry.get_provider`.
"""

from .base import BaseProvider, GenerationStatus, ProviderResult
from .config import ProviderConfig, auth_headers
from .flux import FluxProvider
from .placeholder import PlaceholderProvider
from .registry import REAL_PROVIDERS, get_provider

__all__ = [
    "BaseProvider",
    "GenerationStatus",
    "ProviderResult",
    "FluxProvider",
    "PlaceholderProvider",
    "ProviderConfig",
    "auth_headers",
    "REAL_PROVIDERS",
    "get_provider",
]

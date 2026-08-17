"""Accommodation providers.

Adding a source means implementing `AccommodationProvider` and registering it
here. Nothing else in the pipeline should need to change -- all ONS-specific
logic lives in `onscal.py` and `selection.py`, never in a provider.
"""

from __future__ import annotations

from .base import AccommodationProvider, ProviderError, PropertyQuote, SearchResult
from .mock import MockProvider
from .serpapi_hotels import SerpApiHotelsProvider

__all__ = [
    "AccommodationProvider",
    "PropertyQuote",
    "ProviderError",
    "SearchResult",
    "MockProvider",
    "SerpApiHotelsProvider",
    "build_provider",
]

_PROVIDERS = {
    "serpapi": SerpApiHotelsProvider,
    "mock": MockProvider,
}


def build_provider(name: str, **kwargs) -> AccommodationProvider:
    """Construct a provider by name, for CLI and workflow selection."""
    try:
        return _PROVIDERS[name](**kwargs)
    except KeyError:
        raise ValueError(
            f"unknown provider {name!r}; expected one of {', '.join(sorted(_PROVIDERS))}"
        ) from None

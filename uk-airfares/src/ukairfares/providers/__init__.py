"""Fare providers.

Adding a source means implementing `FareProvider` and registering it here.
Nothing else in the pipeline should need to change.
"""

from __future__ import annotations

from .base import FareProvider, FareQuote, ProviderError, SearchResult
from .mock import MockProvider
from .serpapi import SerpApiProvider
from .travelpayouts import TravelpayoutsProvider

__all__ = [
    "FareProvider",
    "FareQuote",
    "ProviderError",
    "SearchResult",
    "MockProvider",
    "SerpApiProvider",
    "TravelpayoutsProvider",
    "build_provider",
]

_PROVIDERS = {
    "serpapi": SerpApiProvider,
    "travelpayouts": TravelpayoutsProvider,
    "mock": MockProvider,
}


def build_provider(name: str, **kwargs) -> FareProvider:
    """Construct a provider by name, for CLI/workflow selection."""
    try:
        return _PROVIDERS[name](**kwargs)
    except KeyError:
        raise ValueError(
            f"unknown provider {name!r}; expected one of {', '.join(sorted(_PROVIDERS))}"
        ) from None

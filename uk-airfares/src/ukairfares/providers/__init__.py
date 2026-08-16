"""Fare providers.

Adding a source means implementing `FareProvider` and registering it here.
Nothing else in the pipeline should need to change.
"""

from __future__ import annotations

from .base import FareProvider, FareQuote, ProviderError, SearchResult
from .mock import MockProvider
from .travelpayouts import TravelpayoutsProvider

__all__ = [
    "FareProvider",
    "FareQuote",
    "ProviderError",
    "SearchResult",
    "MockProvider",
    "TravelpayoutsProvider",
    "build_provider",
]


def build_provider(name: str, **kwargs) -> FareProvider:
    """Construct a provider by name, for CLI/workflow selection."""
    if name == "travelpayouts":
        return TravelpayoutsProvider(**kwargs)
    if name == "mock":
        return MockProvider(**kwargs)
    raise ValueError(f"unknown provider {name!r}; expected 'travelpayouts' or 'mock'")

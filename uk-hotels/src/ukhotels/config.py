"""Runtime configuration, entirely from environment variables.

No credential ever lands in the repo. In GitHub Actions these come from
encrypted secrets; locally, from your shell or a .env you do not commit.

The methodology knobs (`rate_basis`, `tax_basis`, `collection_alignment`) are
here rather than hardcoded because each is a genuine open question about what
ONS do, and each one silently changes the level of the resulting series. Making
them configurable is not flexibility for its own sake -- it is what lets the
same panel be reprocessed under a different reading of the methodology without
recollecting anything.
"""

from __future__ import annotations

import dataclasses
import os

from .onscal import ADVANCE_DAYS, COLLECTION_ALIGNMENTS, CollectionAlignment
from .selection import DEFAULT_MAX_PRICE_RATIO, RateBasis, TaxBasis

PIPELINE_VERSION = "0.1.0"

_RATE_BASES = ("free_cancellation", "non_refundable", "any")
_TAX_BASES = ("advertised", "before_taxes")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = (os.environ.get(name) or default).strip()
    if value not in allowed:
        raise ConfigError(
            f"{name} must be one of {', '.join(allowed)}; got {value!r}"
        )
    return value


class ConfigError(RuntimeError):
    """Configuration is missing or invalid. Always fatal -- never soldier on."""


#: Which environment variable holds each provider's credential. Adding a
#: provider means adding a line here and one in providers/__init__.py.
PROVIDER_CREDENTIAL_ENV: dict[str, str] = {
    "serpapi": "SERPAPI_KEY",
    "mock": "",  # needs none
}


@dataclasses.dataclass(frozen=True, slots=True)
class Config:
    project: str
    dataset: str
    provider_name: str
    provider_credential: str | None
    market: str
    currency: str

    # --- methodology knobs -------------------------------------------------
    #: Cancellation policy held constant across the series. The largest single
    #: contamination risk in accommodation data.
    rate_basis: RateBasis
    #: Whether `price_gbp` is the advertised or the before-taxes rate. Both are
    #: always stored; this decides which is the headline.
    tax_basis: TaxBasis
    #: Which reading of "collected six weeks in advance for two nights" the
    #: collector follows. Both are computed at reconciliation regardless; this
    #: only decides which nights a given run actually prices.
    collection_alignment: CollectionAlignment
    advance_days: int
    max_price_ratio: float
    #: Below this many comparable properties, a cell is recorded but flagged as
    #: too thin to carry an index on its own.
    min_properties_per_cell: int

    failure_threshold: float
    dry_run: bool
    scrapes_table: str
    index_table: str
    #: BigQuery dataset location. Fixed at dataset creation and immutable
    #: afterwards, so it is set explicitly rather than left to the API default
    #: (which is the US). europe-west2 is London.
    location: str = "europe-west2"

    @classmethod
    def from_env(cls) -> "Config":
        project = os.environ.get("GCP_PROJECT", "").strip()
        dataset = os.environ.get("BQ_DATASET", "").strip()
        provider_name = os.environ.get("HOTEL_PROVIDER", "serpapi").strip()
        dry_run = _env_bool("DRY_RUN", False)

        if not dry_run:
            if not project:
                raise ConfigError("GCP_PROJECT is required (or set DRY_RUN=1)")
            if not dataset:
                raise ConfigError("BQ_DATASET is required (or set DRY_RUN=1)")

        if provider_name not in PROVIDER_CREDENTIAL_ENV:
            raise ConfigError(
                f"unknown HOTEL_PROVIDER {provider_name!r}; expected one of "
                f"{', '.join(sorted(PROVIDER_CREDENTIAL_ENV))}"
            )
        credential_env = PROVIDER_CREDENTIAL_ENV[provider_name]
        credential = (
            os.environ.get(credential_env, "").strip() or None if credential_env else None
        )
        # Deliberately NOT validated here. ensure_tables, reconcile, validate,
        # backfill and digest read BigQuery and ONS and never call a rate
        # provider, so demanding a provider key from them blocks setup for no
        # reason. The pull path calls `require_provider_credential()` first
        # instead, which keeps fail-fast exactly where it matters.

        threshold = _env_float("FAILURE_THRESHOLD", 0.34)
        if not 0.0 <= threshold <= 1.0:
            raise ConfigError(f"FAILURE_THRESHOLD must be in [0,1], got {threshold}")

        alignment = _env_choice(
            "COLLECTION_ALIGNMENT", "per_night", COLLECTION_ALIGNMENTS
        )

        return cls(
            project=project,
            dataset=dataset,
            provider_name=provider_name,
            provider_credential=credential,
            market=os.environ.get("HOTEL_MARKET", "uk").strip(),
            currency=os.environ.get("HOTEL_CURRENCY", "GBP").strip().upper(),
            # DEFAULT IS "any", AND THAT IS A KNOWN COMPROMISE, NOT A CONVENIENCE.
            #
            # Cancellation policy is the largest single contamination risk in
            # accommodation data -- refundable and non-refundable rates for an
            # identical room routinely differ by 30-40%. Controlling for it is
            # what "free_cancellation" does, and it was the original default.
            #
            # It cannot be controlled on this source. A raw-key census over 214
            # live properties found `free_cancellation` in the key set of NONE
            # of them; the only route to it is a nested `prices` array carried
            # by ~17%, which yielded a known value for 6%. Holding the basis
            # constant therefore rejected 100% of every cell and produced no
            # panel at all.
            #
            # So the series is collected with cancellation policy UNCONTROLLED.
            # That is a real bias of unknown sign, and it is treated as one:
            # every row records rate_basis, validate.py raises a standing
            # blocker while it is "any", and the digest reports the refundable
            # mix where it is known so the magnitude can at least be estimated
            # rather than merely acknowledged.
            rate_basis=_env_choice("RATE_BASIS", "any", _RATE_BASES),
            tax_basis=_env_choice("TAX_BASIS", "advertised", _TAX_BASES),
            collection_alignment=alignment,
            advance_days=_env_int("ADVANCE_DAYS", ADVANCE_DAYS),
            max_price_ratio=_env_float("MAX_PRICE_RATIO", DEFAULT_MAX_PRICE_RATIO),
            min_properties_per_cell=_env_int("MIN_PROPERTIES_PER_CELL", 3),
            failure_threshold=threshold,
            dry_run=dry_run,
            scrapes_table=os.environ.get(
                "BQ_SCRAPES_TABLE", "accommodation_scrapes"
            ).strip(),
            index_table=os.environ.get("BQ_INDEX_TABLE", "reconstructed_index").strip(),
            location=os.environ.get("BQ_LOCATION", "europe-west2").strip(),
        )

    def require_provider_credential(self) -> None:
        """Assert the active provider's credential is present.

        Called by the pull path before it issues any queries, so a missing key
        fails immediately rather than after twelve failed location lookups.
        """
        credential_env = PROVIDER_CREDENTIAL_ENV.get(self.provider_name, "")
        if credential_env and not self.provider_credential:
            raise ConfigError(
                f"{credential_env} is required for the {self.provider_name} provider. "
                "Set HOTEL_PROVIDER=mock for a no-network run."
            )

    def table_ref(self, table: str) -> str:
        return f"{self.project}.{self.dataset}.{table}"

    @property
    def scrapes_ref(self) -> str:
        return self.table_ref(self.scrapes_table)

    @property
    def index_ref(self) -> str:
        return self.table_ref(self.index_table)

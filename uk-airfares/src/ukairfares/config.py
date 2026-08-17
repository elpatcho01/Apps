"""Runtime configuration, entirely from environment variables.

No credential ever lands in the repo. In GitHub Actions these come from
encrypted secrets; locally, from your shell or a .env you do not commit.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os

from .onscal import DEFAULT_TARGET_DEPARTURE_TIME

PIPELINE_VERSION = "1.0.0"


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


def _env_time(name: str, default: dt.time) -> dt.time:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return dt.time.fromisoformat(raw.strip())


class ConfigError(RuntimeError):
    """Configuration is missing or invalid. Always fatal -- never soldier on."""


#: Which environment variable holds each provider's credential. Adding a
#: provider means adding a line here and one in providers/__init__.py -- there
#: is deliberately no provider-specific branching anywhere else.
PROVIDER_CREDENTIAL_ENV: dict[str, str] = {
    "travelpayouts": "TRAVELPAYOUTS_TOKEN",
    "serpapi": "SERPAPI_KEY",
    "mock": "",  # needs none
}


@dataclasses.dataclass(frozen=True, slots=True)
class Config:
    project: str
    dataset: str
    provider_name: str
    #: The active provider's credential, whichever provider that is.
    provider_credential: str | None
    market: str
    currency: str
    target_departure_time: dt.time
    #: Fraction of failed queries above which the whole run is treated as
    #: failed. Individual failures are logged and skipped (Task 3), but a run
    #: that mostly failed must exit non-zero and be visible (Task 5) rather than
    #: quietly writing a near-empty vintage.
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
        provider_name = os.environ.get("FARE_PROVIDER", "travelpayouts").strip()
        dry_run = _env_bool("DRY_RUN", False)

        if not dry_run:
            if not project:
                raise ConfigError("GCP_PROJECT is required (or set DRY_RUN=1)")
            if not dataset:
                raise ConfigError("BQ_DATASET is required (or set DRY_RUN=1)")

        if provider_name not in PROVIDER_CREDENTIAL_ENV:
            raise ConfigError(
                f"unknown FARE_PROVIDER {provider_name!r}; expected one of "
                f"{', '.join(sorted(PROVIDER_CREDENTIAL_ENV))}"
            )
        credential_env = PROVIDER_CREDENTIAL_ENV[provider_name]
        credential = (
            os.environ.get(credential_env, "").strip() or None if credential_env else None
        )
        if credential_env and not credential:
            raise ConfigError(
                f"{credential_env} is required for the {provider_name} provider. "
                "Set FARE_PROVIDER=mock for a no-network run."
            )

        threshold = _env_float("FAILURE_THRESHOLD", 0.34)
        if not 0.0 <= threshold <= 1.0:
            raise ConfigError(f"FAILURE_THRESHOLD must be in [0,1], got {threshold}")

        return cls(
            project=project,
            dataset=dataset,
            provider_name=provider_name,
            provider_credential=credential,
            market=os.environ.get("TP_MARKET", "uk").strip(),
            currency=os.environ.get("FARE_CURRENCY", "GBP").strip().upper(),
            target_departure_time=_env_time(
                "TARGET_DEPARTURE_TIME", DEFAULT_TARGET_DEPARTURE_TIME
            ),
            failure_threshold=threshold,
            dry_run=dry_run,
            scrapes_table=os.environ.get("BQ_SCRAPES_TABLE", "airfare_scrapes").strip(),
            index_table=os.environ.get(
                "BQ_INDEX_TABLE", "reconstructed_index"
            ).strip(),
            location=os.environ.get("BQ_LOCATION", "europe-west2").strip(),
        )

    def table_ref(self, table: str) -> str:
        return f"{self.project}.{self.dataset}.{table}"

    @property
    def scrapes_ref(self) -> str:
        return self.table_ref(self.scrapes_table)

    @property
    def index_ref(self) -> str:
        return self.table_ref(self.index_table)

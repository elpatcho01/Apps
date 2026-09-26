"""Runtime configuration from environment variables. No credential in the repo."""

from __future__ import annotations

import dataclasses
import os

from . import PIPELINE_VERSION


class ConfigError(RuntimeError):
    """Missing or invalid configuration. Always fatal."""


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclasses.dataclass(frozen=True)
class Config:
    project: str | None
    dataset: str | None
    location: str
    dry_run: bool
    #: WS1 fan candidate used for the published exposure table. Defaults to the
    #: 5%/simplest winner on the live backtest ("unconditional"); set by the
    #: user's choice of selection rule, see README.
    fan_candidate: str
    #: WS2a elementary-aggregate variant used for the published reconstruction.
    recon_variant: str
    #: WS3: licensed panel vendor adapter name; empty = no vendor, no nowcast.
    panel_vendor: str
    pipeline_version: str = PIPELINE_VERSION

    @classmethod
    def from_env(cls) -> "Config":
        dry = _bool("DRY_RUN")
        project = os.environ.get("GCP_PROJECT") or None
        dataset = os.environ.get("BQ_DATASET") or None
        if not dry and not (project and dataset):
            raise ConfigError("GCP_PROJECT and BQ_DATASET are required unless DRY_RUN=1")
        return cls(
            project=project,
            dataset=dataset,
            location=os.environ.get("BQ_LOCATION", "europe-west2"),
            dry_run=dry,
            fan_candidate=os.environ.get("FAN_CANDIDATE", "unconditional"),
            recon_variant=os.environ.get("RECON_VARIANT", "1dutot_2carli"),
            panel_vendor=os.environ.get("PANEL_VENDOR", ""),
        )

    def table(self, name: str) -> str:
        return f"{self.project}.{self.dataset}.{name}"

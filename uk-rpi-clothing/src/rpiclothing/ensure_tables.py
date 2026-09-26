"""Apply the BigQuery DDL. Idempotent: CREATE ... IF NOT EXISTS and views only.
It cannot alter or drop anything, so no scheduled job can reshape history."""

from __future__ import annotations

import logging
import sys

from .bq import BigQueryWriter
from .config import Config, ConfigError

log = logging.getLogger("rpiclothing.ensure_tables")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}", file=sys.stderr, flush=True)
        return 2
    if config.dry_run:
        log.info("DRY_RUN set; skipping DDL")
        return 0
    try:
        w = BigQueryWriter(config.project)
        w.ensure_dataset(config.project, config.dataset, config.location)
        w.ensure_tables(config.project, config.dataset)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::failed to prepare BigQuery: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

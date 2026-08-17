"""Apply the BigQuery DDL. Idempotent; safe to run before every pull.

Every statement in sql/ is CREATE TABLE IF NOT EXISTS, so this creates the
tables on first run and is a no-op thereafter. It deliberately cannot alter or
drop anything: schema changes are made by editing sql/ and applying them
by hand, so that no scheduled job can ever reshape a table full of history.
"""

from __future__ import annotations

import logging
import sys

from .bq import BigQueryWriter
from .config import Config, ConfigError

log = logging.getLogger("ukairfares.ensure_tables")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}", flush=True)
        return 2

    if config.dry_run:
        log.info("DRY_RUN set; skipping DDL")
        return 0

    try:
        writer = BigQueryWriter(config.project)
        writer.ensure_dataset(config.project, config.dataset, config.location)
        writer.ensure_tables(config.project, config.dataset)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::failed to prepare BigQuery: {exc}", flush=True)
        log.exception("failed to prepare BigQuery")
        return 1

    log.info("dataset and tables ready in %s.%s", config.project, config.dataset)
    return 0


if __name__ == "__main__":
    sys.exit(main())

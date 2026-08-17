"""Create the dataset, tables and views. Idempotent, run before every job.

Every statement in sql/ is CREATE ... IF NOT EXISTS or CREATE OR REPLACE VIEW,
so this is safe to run on every workflow invocation and is -- rather than a
one-off setup step nobody remembers to repeat when a migration lands.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import bq
from .config import Config, ConfigError

log = logging.getLogger("ukhotels.ensure_tables")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the BigQuery dataset, tables and views."
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}", flush=True)
        return 2

    if config.dry_run:
        print("::notice::DRY_RUN set; not touching BigQuery", flush=True)
        return 0

    writer = bq.BigQueryWriter(config.project)
    writer.ensure_dataset(config.project, config.dataset, config.location)
    writer.ensure_tables(config.project, config.dataset)
    log.info("dataset and tables ready in %s.%s", config.project, config.dataset)
    return 0


if __name__ == "__main__":
    sys.exit(main())

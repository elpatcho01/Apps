"""BigQuery access.

Carried over from the air-fares project essentially unchanged, because the
reasoning behind it is not fare-specific: writes go through load jobs rather
than the streaming API (streamed rows sit in a buffer where they are awkward to
query and cannot be partition-pruned promptly, and load jobs into a partitioned
table are free where streaming inserts are not), and everything is WRITE_APPEND
with no update path and no delete path.

The one thing worth reading twice is `ensure_tables`. See the comment there.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
from decimal import Decimal
from typing import Any, Iterable, Protocol

log = logging.getLogger(__name__)

SQL_DIR = pathlib.Path(__file__).resolve().parents[2] / "sql"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (dt.datetime, dt.date, dt.time)):
        return obj.isoformat()
    raise TypeError(f"not JSON serialisable: {type(obj)}")


class Writer(Protocol):
    def append(self, table: str, rows: list[dict[str, Any]]) -> int: ...


class DryRunWriter:
    """Writes newline-delimited JSON to a file (or stdout) instead of BigQuery.

    Lets the full pipeline be exercised in CI and locally with no credentials
    and no billable operations, while producing exactly the payload that would
    have been loaded.
    """

    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = path
        self.written: list[dict[str, Any]] = []

    def append(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        self.written.extend(rows)
        payload = "\n".join(json.dumps(r, default=_json_default) for r in rows)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
            log.info("DRY RUN: %d rows for %s -> %s", len(rows), table, self.path)
        else:
            log.info("DRY RUN: %d rows for %s", len(rows), table)
            log.debug("%s", payload)
        return len(rows)


class BigQueryWriter:
    """Append-only BigQuery writer."""

    def __init__(self, project: str) -> None:
        from google.cloud import bigquery  # imported lazily so dry runs need no dep

        self._bigquery = bigquery
        self._client = bigquery.Client(project=project)

    def append(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            log.info("nothing to write to %s", table)
            return 0

        bigquery = self._bigquery
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            # The tables are created by sql/*.sql; never let a load job invent a
            # schema, or a provider adding a field could silently reshape them.
            autodetect=False,
            schema_update_options=[],
        )
        encoded = [json.loads(json.dumps(r, default=_json_default)) for r in rows]
        job = self._client.load_table_from_json(encoded, table, job_config=job_config)
        job.result()  # raises on failure -- we want that loud
        log.info("appended %d rows to %s", len(rows), table)
        return len(rows)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        bigquery = self._bigquery
        job_config = None
        if params:
            job_config = bigquery.QueryJobConfig(
                query_parameters=[_to_query_param(bigquery, k, v) for k, v in params.items()]
            )
        return [dict(row) for row in self._client.query(sql, job_config=job_config).result()]

    def ensure_dataset(self, project: str, dataset: str, location: str) -> None:
        """Create the dataset if absent. Idempotent.

        Done here rather than left as a manual setup step because the DDL below
        fails with an unhelpful "not found" if the dataset is missing. Location
        is fixed at creation and cannot be changed afterwards, so it is explicit
        rather than defaulted by the API (which would put UK data in the US).
        """
        bigquery = self._bigquery
        ref = f"{project}.{dataset}"
        try:
            existing = self._client.get_dataset(ref)
        except Exception:  # noqa: BLE001 - NotFound, but the type is client-specific
            obj = bigquery.Dataset(ref)
            obj.location = location
            obj.description = (
                "UK accommodation price nowcasting: append-only property panel and "
                "reconstructed ONS sub-indices. Never UPDATE or DELETE."
            )
            self._client.create_dataset(obj, exists_ok=True)
            log.info("created dataset %s in %s", ref, location)
            return

        if existing.location and existing.location.upper() != location.upper():
            log.warning(
                "dataset %s is in %s, not the configured %s. Dataset location "
                "cannot be changed after creation.",
                ref, existing.location, location,
            )
        else:
            log.info("dataset %s already exists", ref)

    def ensure_tables(self, project: str, dataset: str) -> None:
        """Apply the DDL. Idempotent -- every statement is CREATE ... IF NOT EXISTS."""
        for path in sorted(SQL_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            sql = sql.replace("${PROJECT}", project).replace("${DATASET}", dataset)
            log.info("applying %s", path.name)
            # Submit each file whole. BigQuery executes multi-statement scripts
            # natively, so a migration with several ALTERs needs no splitting.
            #
            # Do NOT be tempted to split on ';' for "clearer errors": the column
            # descriptions in these files contain semicolons, so a naive split
            # severs string literals and every statement then fails with an
            # unclosed-literal syntax error. This cost real time on the sibling
            # project and the fix is simply not to do it.
            self._client.query(sql).result()


def _to_query_param(bigquery, name: str, value: Any):
    if isinstance(value, bool):
        return bigquery.ScalarQueryParameter(name, "BOOL", value)
    if isinstance(value, int):
        return bigquery.ScalarQueryParameter(name, "INT64", value)
    if isinstance(value, float):
        return bigquery.ScalarQueryParameter(name, "FLOAT64", value)
    if isinstance(value, dt.datetime):
        return bigquery.ScalarQueryParameter(name, "TIMESTAMP", value)
    if isinstance(value, dt.date):
        return bigquery.ScalarQueryParameter(name, "DATE", value)
    if isinstance(value, Decimal):
        return bigquery.ScalarQueryParameter(name, "NUMERIC", value)
    if isinstance(value, (list, tuple)):
        return bigquery.ArrayQueryParameter(name, "STRING", [str(v) for v in value])
    return bigquery.ScalarQueryParameter(name, "STRING", str(value))


def build_writer(config, dry_run_path: pathlib.Path | None = None) -> Writer:
    if config.dry_run:
        return DryRunWriter(dry_run_path)
    return BigQueryWriter(config.project)


def chunked(items: Iterable[Any], size: int = 500) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch

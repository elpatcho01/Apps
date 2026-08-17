"""The DDL files themselves, and how ensure_tables submits them.

A production run failed with "Unclosed string literal" because ensure_tables
split each file on ';' before submitting. The column descriptions in these
files legitimately contain semicolons, so the split severed string literals.
These tests pin both halves of that lesson.
"""

import pathlib
import re

import pytest

from ukairfares import bq

SQL_FILES = sorted((pathlib.Path(__file__).parent.parent / "sql").glob("*.sql"))


class FakeQueryJob:
    def result(self):
        return None


class FakeClient:
    def __init__(self):
        self.queries: list[str] = []

    def query(self, sql, job_config=None):
        self.queries.append(sql)
        return FakeQueryJob()


def make_writer():
    writer = bq.BigQueryWriter.__new__(bq.BigQueryWriter)
    writer._client = FakeClient()
    writer._bigquery = None
    return writer


class TestDdlFiles:
    def test_files_exist(self):
        assert len(SQL_FILES) >= 4

    def test_descriptions_contain_semicolons(self):
        """The exact property that makes splitting on ';' wrong."""
        joined = "\n".join(f.read_text() for f in SQL_FILES)
        assert re.search(r'description="[^"]*;[^"]*"', joined), (
            "expected at least one column description containing a semicolon"
        )

    def test_every_statement_has_balanced_quotes(self):
        for f in SQL_FILES:
            assert f.read_text().count('"') % 2 == 0, f"unbalanced quotes in {f.name}"

    def test_placeholders_are_substituted(self):
        for f in SQL_FILES:
            text = f.read_text()
            assert "${PROJECT}" in text and "${DATASET}" in text, f.name

    def test_creates_are_idempotent(self):
        for f in SQL_FILES:
            text = f.read_text().upper()
            for verb, guard in (("CREATE TABLE", "IF NOT EXISTS"),
                                ("ADD COLUMN", "IF NOT EXISTS")):
                if verb in text:
                    assert guard in text, f"{f.name}: {verb} without {guard}"

    def test_no_destructive_statements(self):
        """Append-only means the DDL must never drop or delete."""
        for f in SQL_FILES:
            text = f.read_text().upper()
            for forbidden in ("DROP TABLE", "DELETE FROM", "TRUNCATE"):
                assert forbidden not in text, f"{f.name} contains {forbidden}"


class TestEnsureTablesSubmission:
    def test_submits_each_file_whole(self):
        writer = make_writer()
        writer.ensure_tables("proj", "ds")
        # One query per file -- never split into per-statement fragments.
        assert len(writer._client.queries) == len(SQL_FILES)

    def test_substitutes_project_and_dataset(self):
        writer = make_writer()
        writer.ensure_tables("proj", "ds")
        for q in writer._client.queries:
            assert "${PROJECT}" not in q and "${DATASET}" not in q
            assert "proj.ds." in q

    def test_semicolon_bearing_descriptions_survive_intact(self):
        writer = make_writer()
        writer.ensure_tables("proj", "ds")
        joined = "\n".join(writer._client.queries)
        assert "Equals index_month_departure; kept under the original spec name." in joined

    def test_files_applied_in_order(self):
        writer = make_writer()
        writer.ensure_tables("proj", "ds")
        # 004 migrates tables created by 002/003, so order matters.
        first_alter = next(
            (i for i, q in enumerate(writer._client.queries) if "ADD COLUMN" in q.upper()),
            None,
        )
        assert first_alter is not None
        assert all(
            "CREATE TABLE" in q.upper()
            for q in writer._client.queries[:first_alter]
        )


class TestPanelQueryDeduplicates:
    """A collection date can carry several runs; reconciliation must pick one.

    airfare_scrapes is append-only, so a manual re-run, a retry, or a
    double-click on dispatch all leave multiple complete vintages for the same
    scrape_date. Aggregating across them counts every route once per run --
    n_observations inflates and a duplicated day carries more weight than a
    clean one.
    """

    def test_query_restricts_to_a_single_run(self):
        from ukairfares.reconcile import PANEL_QUERY

        sql = PANEL_QUERY.format(table="p.d.airfare_scrapes")
        assert "latest_run" in sql
        assert "run_id = (SELECT run_id FROM latest_run)" in sql

    def test_latest_run_is_chosen_by_scrape_ts(self):
        from ukairfares.reconcile import PANEL_QUERY

        sql = PANEL_QUERY.format(table="p.d.airfare_scrapes")
        assert "ORDER BY scrape_ts DESC" in sql and "LIMIT 1" in sql

    def test_still_filters_to_usable_rows(self):
        from ukairfares.reconcile import PANEL_QUERY

        sql = PANEL_QUERY.format(table="p.d.airfare_scrapes")
        assert "status = 'ok'" in sql and "price_gbp IS NOT NULL" in sql

"""The DDL files themselves, and how ensure_tables submits them.

A production run failed with "Unclosed string literal" because ensure_tables
split each file on ';' before submitting. The column descriptions in these
files legitimately contain semicolons, so the split severed string literals.
These tests pin both halves of that lesson.

A later run failed the other way. 009 added five columns as five ALTER
statements, and BigQuery allows five table metadata update operations per table
per ten seconds -- so the fourth returned "Exceeded rate limits" and
ensure_tables exited non-zero. Every workflow prepares the schema first, so in
the daily pull that would have cost a collection day, and a missed index day
cannot be recollected. Two tests below keep it from recurring: migrations stay
under the limit, and a run that meets it anyway waits rather than dies.
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


class TestMetadataRateLimit:
    """BigQuery: 5 table metadata update operations per table per 10 seconds."""

    MAX_PER_TABLE = 4

    def _alters_by_table(self, text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for line in text.splitlines():
            if line.strip().startswith("--"):
                continue  # a comment describing ALTERs is not an ALTER
            match = re.search(r"ALTER TABLE\s+`[^`]*\.([A-Za-z0-9_]+)`", line)
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + 1
        return counts

    def test_no_file_alters_one_table_more_than_four_times(self):
        """Five ALTERs on one table in one script is what broke the digest.

        A single ALTER with several ADD COLUMN clauses is ONE metadata operation,
        so a migration adding any number of columns can stay well under the cap.
        """
        for f in SQL_FILES:
            for table, n in self._alters_by_table(f.read_text()).items():
                assert n <= self.MAX_PER_TABLE, (
                    f"{f.name} issues {n} ALTERs against {table}; BigQuery allows "
                    f"5 per 10s. Combine them into one ALTER with several "
                    f"ADD COLUMN clauses."
                )

    def test_the_migration_that_broke_it_is_now_a_single_statement(self):
        text = (pathlib.Path(__file__).parent.parent / "sql"
                / "009_add_price_relative.sql").read_text()
        assert self._alters_by_table(text) == {"reconstructed_index": 1}
        assert text.upper().count("ADD COLUMN IF NOT EXISTS") == 5


class RateLimitedClient:
    """Fails with BigQuery's rate-limit message a given number of times first."""

    MESSAGE = ("400 GET https://bigquery.googleapis.com/...: Exceeded rate limits: "
               "too many table update operations for this table. at [52:1]")

    def __init__(self, failures: int, error: Exception | None = None):
        self.failures = failures
        self.error = error
        self.calls = 0

    def query(self, sql, job_config=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error or ValueError(self.MESSAGE)
        return FakeQueryJob()


class TestEnsureTablesWaitsOutTheLimit:
    def test_a_rate_limited_file_is_retried_rather_than_fatal(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(bq.time, "sleep", slept.append)
        writer = make_writer()
        writer._client = RateLimitedClient(failures=2)
        writer.ensure_tables("proj", "ds")
        assert writer._client.calls == len(SQL_FILES) + 2
        assert slept == [bq.DDL_RETRY_SECONDS, bq.DDL_RETRY_SECONDS]

    def test_it_gives_up_rather_than_retrying_forever(self, monkeypatch):
        monkeypatch.setattr(bq.time, "sleep", lambda _: None)
        writer = make_writer()
        writer._client = RateLimitedClient(failures=99)
        with pytest.raises(ValueError, match="Exceeded rate limits"):
            writer.ensure_tables("proj", "ds")
        assert writer._client.calls == bq.MAX_DDL_ATTEMPTS

    def test_a_real_error_fails_immediately(self, monkeypatch):
        """Retrying a syntax error four times just delays the same failure."""
        monkeypatch.setattr(bq.time, "sleep", lambda _: None)
        writer = make_writer()
        writer._client = RateLimitedClient(
            failures=99, error=ValueError("Syntax error: Unclosed string literal"))
        with pytest.raises(ValueError, match="Unclosed string"):
            writer.ensure_tables("proj", "ds")
        assert writer._client.calls == 1


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


class TestCurrentScrapesView:
    """The alternative to deleting superseded runs.

    Three runs landed for 2026-08-17, two of them carrying a selection bug. The
    append-only invariant means they stay; the view is what stops a naive query
    averaging across them.
    """

    def _view_sql(self) -> str:
        path = next(f for f in SQL_FILES if "current_scrapes" in f.name)
        return path.read_text()

    def test_view_exists(self):
        assert any("current_scrapes" in f.name for f in SQL_FILES)

    def test_is_a_view_not_a_table_copy(self):
        sql = self._view_sql().upper()
        assert "CREATE OR REPLACE VIEW" in sql
        assert "CREATE TABLE" not in sql

    def test_selects_one_run_per_date(self):
        sql = self._view_sql()
        assert "PARTITION BY scrape_date ORDER BY scrape_ts DESC" in sql
        assert "WHERE rn = 1" in sql

    def test_matches_the_reconcile_semantics(self):
        """View and reconciliation must agree on which vintage is current."""
        from ukairfares.reconcile import PANEL_QUERY

        panel_sql = PANEL_QUERY.format(table="p.d.airfare_scrapes")
        # Both pick the latest run for the date, by scrape_ts.
        assert "ORDER BY scrape_ts DESC" in panel_sql
        assert "ORDER BY scrape_ts DESC" in self._view_sql()

    def test_does_not_filter_status(self):
        """Coverage analysis needs error and no_data rows, so the view keeps them."""
        assert "status" not in self._view_sql()

    def test_view_is_still_non_destructive(self):
        sql = self._view_sql().upper()
        for forbidden in ("DROP TABLE", "DELETE FROM", "TRUNCATE"):
            assert forbidden not in sql


class TestViewsApplyAfterMigrations:
    """`SELECT *` in a view is frozen at creation time, so ordering is load-bearing.

    BigQuery resolves the star when the view is created and stores the resulting
    column list; it does not re-expand on read. A view created before a migration
    that adds a column therefore does not have that column, and stays that way
    until it is recreated.

    That is why the view is numbered 900 rather than 006. As 006 it ran BEFORE
    007_add_selection_margin, so the new column would have been in the table and
    missing from every consumer -- the digest, the export, the dashboard -- for a
    full run. This project has already lost time to exactly that shape of
    problem once, when current_scrapes returned NotFound because the DDL had not
    caught up with the code reading it.
    """

    def test_every_view_sorts_after_every_table_and_migration(self):
        views = [p for p in SQL_FILES if "VIEW" in p.read_text().upper()]
        others = [p for p in SQL_FILES if p not in views]
        assert views, "expected at least one view"
        assert others, "expected table DDL"
        assert max(SQL_FILES.index(v) for v in views) > max(
            SQL_FILES.index(o) for o in others
        ), "a view is applied before a table migration; its SELECT * will be stale"

    def test_views_live_in_the_reserved_band(self):
        for p in SQL_FILES:
            if "VIEW" in p.read_text().upper():
                assert p.name[0] == "9", (
                    f"{p.name}: views belong in the 900 band so they always sort last"
                )

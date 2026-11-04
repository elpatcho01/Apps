"""One definition of "current", and the join vocabulary. No network.

The SQL here cannot be executed offline -- that is exactly how the "all"/"uk"
mismatch and the missing class filter survived: every validation test stubs the
reader and returns rows that are already joined. So these tests pin the
STRUCTURE of the SQL, and `validate --self-check` runs it for real in Actions.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest

from ukhotels import latest, validate

SRC = pathlib.Path(latest.__file__).parent


# --- the definitions ---------------------------------------------------------


def test_latest_published_ranks_within_the_documented_key_by_fetch_time():
    sql = latest.latest_published("p.d.t")
    assert "PARTITION BY index_month, location, series_source, coicop_class" in sql
    assert "ORDER BY fetched_ts DESC" in sql
    assert "_vintage_rank = 1" in sql


def test_latest_reconstruction_ranks_within_every_variant_column():
    """A rescore under a different reading is a different number, not a vintage."""
    sql = latest.latest_reconstruction("p.d.t")
    for column in latest.RECONSTRUCTION_KEY:
        assert column in sql
    assert "ORDER BY computed_ts DESC" in sql


def test_excluded_series_are_filtered_with_ifnull():
    """`NULL NOT IN (...)` is NULL -- it would drop every ad hoc regional row."""
    sql = latest.latest_published("p.d.t")
    assert "IFNULL(series_id, '') NOT IN ('d7ex')" in sql


def test_a_where_clause_filters_before_ranking_not_after():
    """Otherwise a newer NULL vintage hides an older usable one."""
    sql = latest.latest_published("p.d.t", where="index_value IS NOT NULL")
    inner, outer = sql.split("_vintage_rank = 1")[0], sql.split("_vintage_rank = 1")[-1]
    assert "index_value IS NOT NULL" in inner
    assert "index_value IS NOT NULL" not in outer


def test_the_national_translation():
    assert latest.published_location_for("all") == "uk"
    assert latest.published_location_for("london") == "london"
    sql = latest.published_location_sql("r.location")
    assert "WHEN r.location = 'all' THEN 'uk'" in sql


# --- nobody trusts the stored flag -------------------------------------------


@pytest.mark.parametrize("module", ["digest", "export", "validate"])
def test_no_reader_filters_on_the_stored_is_current_flag(module):
    """The flag is written True on every row and never cleared: it means nothing.

    In the 2026-11 export, 783 of 786 published keys had three "current" rows.
    The only remaining mention allowed is the self-check's diagnostic count of
    how wrong the flag is, which reads it on purpose.
    """
    text = (SRC / f"{module}.py").read_text(encoding="utf-8")
    assert not re.search(r"WHERE\s+is_current\b", text), f"{module} filters on is_current"
    assert not re.search(r"AND\s+is_current\b", text), f"{module} filters on is_current"


@pytest.mark.parametrize("module,literal", [
    ("reconcile", '"location": "all"'),
    ("backfill", 'location="uk"'),
])
def test_national_location_spellings_come_from_the_shared_constants(module, literal):
    """The mismatch existed because each side spelled its own name for "national"."""
    text = (SRC / f"{module}.py").read_text(encoding="utf-8")
    assert literal not in text


# --- the scoring join --------------------------------------------------------


def test_the_score_query_translates_the_national_location():
    sql = validate.score_query(recon_source="(SELECT 1)", published_table="p.d.t")
    assert "p.location = (CASE WHEN r.location = 'all' THEN 'uk'" in sql


def test_the_score_query_filters_to_the_target_class():
    """Without this, each national row joins hotels, campsites and the aggregate."""
    sql = validate.score_query(recon_source="(SELECT 1)", published_table="p.d.t")
    assert "p.coicop_class = @coicop_class" in sql
    assert latest.TARGET_COICOP == "11.2.0.1"


def test_the_score_query_reads_published_values_through_latest():
    sql = validate.score_query(recon_source="(SELECT 1)", published_table="p.d.t")
    assert latest.latest_published("p.d.t", where="index_value IS NOT NULL") in sql


def test_the_synthetic_rows_carry_every_column_the_join_reads():
    """If SCORE_QUERY grows an r.column, the live self-check must not break silently."""
    read = set(re.findall(r"\br\.([a-z_]+)", validate.SCORE_QUERY))
    supplied = {"index_month", "location"} | {n for n, _ in validate._SYNTHETIC_COLUMNS}
    assert read <= supplied, f"synthetic rows lack {sorted(read - supplied)}"


def test_synthetic_rows_are_literals_and_touch_no_table():
    sql = validate.synthetic_recon_source([(dt.date(2026, 8, 1), "all")])
    assert sql.startswith("(SELECT * FROM UNNEST([STRUCT(")
    assert "DATE '2026-08-01' AS index_month" in sql
    assert "FROM `" not in sql


# --- the self-check's own logic, against a stub ------------------------------


class _Reader:
    """Answers the self-check's queries; `per_month` copies of each joined row."""

    def __init__(self, *, per_month=1, coicop="11.2.0.1", location_for=None,
                 months=None, excluded_visible=0):
        self.per_month = per_month
        self.coicop = coicop
        self.location_for = location_for or {"timeseries": "uk", "adhoc_regional": "london"}
        self.months = months if months is not None else [
            dt.date(2026, 9, 1), dt.date(2026, 8, 1), dt.date(2026, 7, 1)]
        self.excluded_visible = excluded_visible
        self._source = None

    def query(self, sql, params=None):
        if "SELECT DISTINCT index_month" in sql:
            self._source = params["series_source"]
            return [{"index_month": m} for m in self.months]
        if "raw_rows_flagged_current" in sql:
            return [{"raw_rows": 2349, "raw_rows_flagged_current": 2349,
                     "latest_rows": 786, "excluded_rows_in_table": 1389,
                     "excluded_rows_visible": self.excluded_visible}]
        return [
            {"index_month": m, "coicop_class": self.coicop,
             "published_location": self.location_for[self._source]}
            for m in self.months for _ in range(self.per_month)
        ]


def _config():
    from tests.test_pipeline import make_config
    return make_config()


def test_self_check_passes_when_each_row_joins_exactly_once():
    result = validate.run_self_check(_config(), reader=_Reader())
    assert result["self_check"] == "PASS", result["failures"]


def test_self_check_fails_on_a_fan_out_join():
    """Three published rows per reconstruction: the missing-class-filter symptom."""
    result = validate.run_self_check(_config(), reader=_Reader(per_month=3))
    assert result["self_check"] == "FAIL"
    assert any("joined 3" in f for f in result["failures"])


def test_self_check_fails_when_nothing_joins():
    """Zero published rows per reconstruction: the "all"/"uk" symptom."""
    result = validate.run_self_check(_config(), reader=_Reader(per_month=0))
    assert result["self_check"] == "FAIL"
    assert any("joined 0" in f for f in result["failures"])


def test_self_check_fails_on_the_wrong_class():
    result = validate.run_self_check(_config(), reader=_Reader(coicop="11.1.2"))
    assert result["self_check"] == "FAIL"


def test_self_check_fails_if_an_excluded_series_is_still_visible():
    result = validate.run_self_check(_config(), reader=_Reader(excluded_visible=5))
    assert result["self_check"] == "FAIL"


def test_a_self_check_that_verified_nothing_does_not_say_pass():
    """Nothing published yet: not a failure, and emphatically not a pass."""
    result = validate.run_self_check(_config(), reader=_Reader(months=[]))
    assert result["self_check"] == "NOTHING_CHECKED"
    assert result["failures"] == []
    assert len(result["skipped"]) == 2


def test_nothing_checked_exits_zero_but_a_failure_exits_one(monkeypatch, capsys):
    from ukhotels import config as config_mod

    monkeypatch.setattr(config_mod.Config, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(validate, "run_self_check",
                        lambda c: {"self_check": "NOTHING_CHECKED", "failures": [], "skipped": []})
    assert validate.main(["--self-check"]) == 0
    monkeypatch.setattr(validate, "run_self_check",
                        lambda c: {"self_check": "FAIL", "failures": ["x"], "skipped": []})
    assert validate.main(["--self-check"]) == 1

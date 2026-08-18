"""Static checks on the SQL, the panel and the property sample.

None of these need BigQuery. They exist because the failures they catch are
cheap to prevent and expensive to notice in production.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from ukhotels import bq, panel, selection

SQL_FILES = sorted((pathlib.Path(__file__).resolve().parents[1] / "sql").glob("*.sql"))


def test_sql_files_exist_and_are_ordered():
    assert SQL_FILES
    assert [p.name[:3] for p in SQL_FILES] == sorted(p.name[:3] for p in SQL_FILES)


def _statement_starts(path) -> list[str]:
    """Lines that begin a DDL statement, ignoring comments and string literals.

    Statement keywords only count at the start of a line; the same words appear
    inside column descriptions ("Never UPDATE or DELETE"), which is prose about
    the invariant rather than a violation of it.
    """
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    ]
    return [line for line in lines if re.match(r"(?i)^\s*(create|alter|delete|update|truncate|drop)\b", line)]


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_every_statement_is_idempotent(path):
    # Applied at the start of every run, so a non-idempotent statement would
    # fail every run after the first.
    statements = _statement_starts(path)
    assert statements, f"no DDL statements found in {path.name}"
    for statement in statements:
        upper = statement.upper()
        assert "IF NOT EXISTS" in upper or "OR REPLACE" in upper, statement


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_mutation_of_the_append_only_panel(path):
    for statement in _statement_starts(path):
        assert not re.match(
            r"(?i)^\s*(delete|update|truncate|drop)\b", statement
        ), f"mutating statement in {path.name}: {statement.strip()}"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_column_descriptions_contain_semicolons_so_splitting_on_them_is_unsafe(path):
    # A standing reminder of the bug: a naive split on ';' severs these string
    # literals and every statement then fails with an unclosed literal. If this
    # test ever fails it means the descriptions were sanitised, which does not
    # make splitting safe -- `bq.ensure_tables` submits each file whole.
    if path.name.startswith(("001", "002", "003")):
        assert ";" in re.sub(r"(?m)^\s*--.*$", "", path.read_text(encoding="utf-8"))


def test_ensure_tables_submits_whole_files():
    source = pathlib.Path(bq.__file__).read_text(encoding="utf-8")
    assert 'split(";")' not in source
    assert "split(';')" not in source


def test_partition_and_cluster_keys_match_what_queries_filter_on():
    scrapes = next(p for p in SQL_FILES if "accommodation_scrapes" in p.name).read_text()
    assert "PARTITION BY scrape_date" in scrapes
    assert "CLUSTER BY location, stay_night_kind, property_tier" in scrapes

    recon = next(p for p in SQL_FILES if "reconstructed_index" in p.name).read_text()
    assert "PARTITION BY index_month" in recon


def test_current_scrapes_view_selects_the_latest_run_per_date():
    view = next(p for p in SQL_FILES if "current_scrapes" in p.name).read_text()
    assert "ROW_NUMBER() OVER (PARTITION BY scrape_date ORDER BY scrape_ts DESC)" in view
    assert "WHERE rn = 1" in view


# --- panel ------------------------------------------------------------------


def test_one_location_per_ons_region_and_no_duplicates():
    codes = [loc.code for loc in panel.LOCATIONS]
    assert len(codes) == len(set(codes)) == 12


def test_every_location_has_a_rationale():
    # Keeps the sample's composition auditable rather than folkloric.
    for loc in panel.LOCATIONS:
        assert loc.rationale.strip()


def test_occupancy_and_stay_length_are_constants_not_configuration():
    # Changing either mid-series would put a step change into the index that no
    # later analysis could unpick.
    assert panel.ADULTS == 2
    assert panel.CHILDREN == 0
    assert panel.NIGHTS == 1


@pytest.mark.parametrize(
    "hotel_class,expected",
    [(3.0, "midscale"), (3.5, "midscale"), (4.0, "upscale"),
     (4.5, "upscale"), (5.0, None), (2.0, None), (None, None)],
)
def test_star_tiers_do_not_overlap_or_leak(hotel_class, expected):
    assert panel.tier_for_class(hotel_class) == expected


def test_query_cost_is_one_call_per_location_per_night():
    assert panel.expected_queries_per_collection_day(1) == 12
    assert panel.expected_queries_per_collection_day(2) == 24


# --- weights ----------------------------------------------------------------


def test_committed_weights_are_flagged_as_placeholders():
    weights = panel.load_weights(2026, allow_placeholder=True)
    assert weights.is_placeholder is True
    assert set(weights.by_region) == {loc.code for loc in panel.LOCATIONS}


def test_placeholder_weights_are_refused_by_the_validation_path():
    # Shipping invented numbers that look authoritative is worse than shipping
    # none, so this refusal is the point of the whole weights module.
    with pytest.raises(ValueError, match="placeholders"):
        panel.load_weights(2026)


def test_weights_normalise_to_one():
    weights = panel.load_weights(2026, allow_placeholder=True)
    assert sum(weights.normalised().values()) == pytest.approx(1.0)


def test_weights_carry_forward_from_the_most_recent_earlier_year():
    # Correct rather than lazy: CPI weights are set annually and held within
    # the year, so the latest year at or before the target is the right one.
    assert panel.load_weights(2027, allow_placeholder=True).year == 2026
    assert panel.load_weights(2025, allow_placeholder=True).year == 2025


# --- property panel ---------------------------------------------------------


def test_an_absent_property_panel_is_not_an_error(tmp_path):
    # Refusing to collect because no sample is pinned would lose stay nights
    # that cannot be re-priced. Census-only mode still yields a usable index.
    assert panel.load_property_panel(tmp_path / "missing.csv") == {}


def test_property_panel_round_trips(tmp_path):
    path = tmp_path / "property_panel.csv"
    props = [
        panel.PanelProperty("london", "upscale", "tokB", "B Hotel", "2026-08-17", "discovered"),
        panel.PanelProperty("london", "upscale", "tokA", "A Hotel", "2026-08-17", "discovered"),
    ]
    panel.write_property_panel(props, path)
    loaded = panel.load_property_panel(path)
    assert {p.property_token for p in loaded[("london", "upscale")]} == {"tokA", "tokB"}
    assert panel.panel_tokens(loaded) == {"tokA", "tokB"}


def test_property_panel_is_written_diff_stably(tmp_path):
    path = tmp_path / "p.csv"
    props = [
        panel.PanelProperty("london", "upscale", "t2", "B", "2026-08-17", "discovered"),
        panel.PanelProperty("london", "upscale", "t1", "A", "2026-08-17", "discovered"),
    ]
    panel.write_property_panel(props, path)
    first = path.read_text(encoding="utf-8")
    panel.write_property_panel(list(reversed(props)), path)
    assert path.read_text(encoding="utf-8") == first


# --- cross-project namespace ------------------------------------------------


def _objects(sql_dir: pathlib.Path) -> set[str]:
    """Every BigQuery object a project's DDL creates."""
    pattern = re.compile(r"\$\{PROJECT\}\.\$\{DATASET\}\.([a-z_]+)")
    return {
        name
        for path in sql_dir.glob("*.sql")
        for name in pattern.findall(path.read_text(encoding="utf-8"))
    }


def test_no_object_name_collides_with_the_air_fares_project():
    """Both projects live in one repo and may share a BigQuery dataset.

    This is not hypothetical. On 2026-08-18 the accommodation reconciliation
    failed with `400 Unrecognized name: location` because `reconstructed_index`
    already existed in the dataset with the AIR FARES schema, so
    `CREATE TABLE IF NOT EXISTS` did nothing and every hotels query hit the
    wrong table. Three names collided: reconstructed_index, ons_published_index
    and current_scrapes.

    The third was the dangerous one. `current_scrapes` is created with
    CREATE OR REPLACE VIEW, which is emphatically *not* a no-op -- running the
    accommodation DDL against a shared dataset would have silently repointed the
    air fares view at `accommodation_scrapes` and broken a live pipeline that was
    collecting daily. It had not happened yet only because the workflow that
    applies the DDL had not run since the merge.

    Every accommodation object is therefore prefixed. Sharing a dataset is now
    safe rather than merely discouraged.
    """
    here = pathlib.Path(__file__).resolve().parents[1] / "sql"
    airfares = here.parents[1] / "uk-airfares" / "sql"
    if not airfares.is_dir():  # pragma: no cover - sibling project absent
        pytest.skip("uk-airfares not present in this checkout")

    ours, theirs = _objects(here), _objects(airfares)
    assert ours, "no objects found in uk-hotels/sql"
    assert theirs, "no objects found in uk-airfares/sql"
    assert not (ours & theirs), (
        f"BigQuery object name(s) shared with uk-airfares: {sorted(ours & theirs)}. "
        "The two projects may share a dataset; a shared name means one project's "
        "CREATE IF NOT EXISTS silently binds to the other's table, and a shared "
        "VIEW name means CREATE OR REPLACE overwrites it outright."
    )


def test_every_accommodation_object_is_namespaced():
    here = pathlib.Path(__file__).resolve().parents[1] / "sql"
    for name in _objects(here):
        assert name.startswith("accommodation_"), (
            f"{name} is not namespaced; it could collide with a sibling project "
            "sharing the dataset"
        )

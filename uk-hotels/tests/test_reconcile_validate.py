"""Reconciliation and validation tests, against a stub BigQuery reader."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from ukhotels import bq, onscal, reconcile, validate
from ukhotels.onsfetch import IndexDayResult
from tests.test_pipeline import make_config

INDEX_DAY = dt.date(2026, 8, 11)  # 2nd Tuesday of August 2026


class StubReader:
    """Answers the three queries reconcile issues, by shape rather than by text."""

    def __init__(self, *, panel_rows=None, dates=None, first_day=None):
        self._panel_rows = panel_rows if panel_rows is not None else []
        self._dates = dates or []
        self._first_day = first_day
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        if "MIN(scrape_date)" in sql:
            return [{"first_day": self._first_day}]
        if "GROUP BY scrape_date" in sql:
            return [{"scrape_date": d, "n": 10} for d in self._dates]
        return self._panel_rows


def panel_row(**overrides):
    row = {
        "scrape_date": INDEX_DAY - dt.timedelta(days=42),
        "location": "london",
        "property_token": "tok1",
        "property_name": "Hotel One",
        "property_tier": "upscale",
        "stay_night_kind": "index_week",
        "collection_alignment": "per_night",
        "check_in": INDEX_DAY,
        "index_day": INDEX_DAY,
        "index_month_stay": dt.date(2026, 8, 1),
        "index_month_collection": dt.date(2026, 6, 1),
        "price_gbp": Decimal("150"),
        "price_before_taxes_gbp": Decimal("125"),
        "price_cheapest_gbp": Decimal("120"),
        "is_panel_property": True,
        "is_cached_source": False,
        "board_basis": None,
        "rate_basis": "free_cancellation",
        "tax_basis": "advertised",
        "status": "ok",
    }
    row.update(overrides)
    return row


def index_day_result():
    return IndexDayResult(
        index_month=dt.date(2026, 8, 1),
        index_day=INDEX_DAY,
        ordinal=2,
        source_url="",
        evidence="test",
    )


# --- implied collection dates ----------------------------------------------


def test_implied_collection_dates_precede_the_index_month():
    dates = reconcile.implied_collection_dates(INDEX_DAY)
    assert dates
    assert all(d < dt.date(2026, 8, 1) for d in dates)
    # per_night for both nights, plus single_day (which coincides with the
    # index-week night's per_night date), so three distinct dates.
    assert dates == sorted(set(dates))
    assert INDEX_DAY - dt.timedelta(days=42) in dates


def test_resolve_prefers_exact_dates_and_reports_zero_offset():
    implied = reconcile.implied_collection_dates(INDEX_DAY)
    reader = StubReader(dates=implied)
    used, offset = reconcile.resolve_collection_dates(reader, "t", INDEX_DAY)
    assert set(used) == set(implied)
    assert offset == 0


def test_resolve_substitutes_a_nearby_date_and_records_the_drift():
    implied = reconcile.implied_collection_dates(INDEX_DAY)
    shifted = [d + dt.timedelta(days=2) for d in implied]
    reader = StubReader(dates=shifted)
    used, offset = reconcile.resolve_collection_dates(reader, "t", INDEX_DAY)
    assert offset == 2
    assert set(used) == set(shifted)


# --- absence versus failure -------------------------------------------------


def test_month_predating_the_panel_is_not_an_error():
    # Trap 6: an absence nobody can fix must not produce a red run.
    reader = StubReader(dates=[], first_day=dt.date(2026, 9, 1))
    with pytest.raises(reconcile.NoCollectionYet, match="predates the panel"):
        reconcile.run_reconcile(
            make_config(),
            index_month=dt.date(2026, 8, 1),
            index_day_override=INDEX_DAY,
            reader=reader,
            writer=bq.DryRunWriter(),
        )


def test_empty_panel_is_not_an_error_either():
    reader = StubReader(dates=[], first_day=None)
    with pytest.raises(reconcile.NoCollectionYet, match="no successful scrapes at all"):
        reconcile.run_reconcile(
            make_config(),
            index_month=dt.date(2026, 8, 1),
            index_day_override=INDEX_DAY,
            reader=reader,
            writer=bq.DryRunWriter(),
        )


def test_a_gap_during_active_collection_is_an_error():
    # The opposite case, which looks identical from inside a failing reconcile
    # and means the collector broke.
    reader = StubReader(dates=[], first_day=dt.date(2025, 1, 1))
    with pytest.raises(RuntimeError, match="Did the collector run"):
        reconcile.run_reconcile(
            make_config(),
            index_month=dt.date(2026, 8, 1),
            index_day_override=INDEX_DAY,
            reader=reader,
            writer=bq.DryRunWriter(),
        )


# --- aggregation ------------------------------------------------------------


def build_rows():
    rows = []
    for i, (location, tier) in enumerate(
        [("london", "upscale"), ("london", "midscale"), ("scotland", "upscale")]
    ):
        for j in range(3):
            rows.append(
                panel_row(
                    location=location,
                    property_tier=tier,
                    property_token=f"tok{i}{j}",
                    price_gbp=Decimal(100 + 10 * i + j),
                )
            )
    return rows


def aggregate(rows=None, **kwargs):
    return reconcile.aggregate(
        rows if rows is not None else build_rows(),
        index_day=index_day_result(),
        scrape_dates_used=[INDEX_DAY - dt.timedelta(days=42)],
        offset_days=0,
        config=make_config(),
        run_id="r",
        computed_ts=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
        **kwargs,
    )


def test_every_methodology_variant_is_produced_and_tagged():
    out = aggregate()
    assert {r["attribution_rule"] for r in out} == set(reconcile.ATTRIBUTION_RULES)
    assert {r["sample_rule"] for r in out} == set(reconcile.SAMPLE_RULES)
    assert {r["agg_method"] for r in out} == set(reconcile.AGG_METHODS)
    # Both scopes on both dimensions.
    assert {r["property_tier"] for r in out} >= {"upscale", "midscale", "all"}
    assert {r["stay_night_kind"] for r in out} >= {"index_week", "both"}


def test_the_two_attribution_rules_land_in_different_months():
    out = aggregate()
    stay = {r["index_month"] for r in out if r["attribution_rule"] == "stay_month"}
    collection = {
        r["index_month"] for r in out if r["attribution_rule"] == "collection_month"
    }
    assert stay == {dt.date(2026, 8, 1)}
    assert collection == {dt.date(2026, 6, 1)}


def test_pinned_panel_and_census_diverge_when_a_property_is_unpinned():
    rows = build_rows()
    rows.append(
        panel_row(
            location="london", property_tier="upscale",
            property_token="stranger", price_gbp=Decimal("900"),
            is_panel_property=False,
        )
    )
    out = aggregate(rows)

    def value(sample_rule):
        return next(
            float(r["reconstructed_value"]) for r in out
            if r["location"] == "london" and r["property_tier"] == "upscale"
            and r["stay_night_kind"] == "index_week"
            and r["attribution_rule"] == "stay_month"
            and r["sample_rule"] == sample_rule and r["agg_method"] == "mean"
        )

    assert value("matched_census") > value("pinned_panel")


def test_board_basis_known_is_false_because_no_provider_reports_it():
    out = aggregate()
    assert all(r["board_basis_known"] is False for r in out)


def test_weighted_aggregate_is_skipped_when_regions_are_missing():
    # A two-region aggregate weighted as if it covered twelve would be
    # misleading in a direction nobody could later detect.
    out = aggregate()
    assert not [r for r in out if r["location"] == "all"]


def test_weighted_aggregate_is_produced_when_every_region_is_present():
    from ukhotels import panel as panel_mod

    rows = []
    for loc in panel_mod.LOCATIONS:
        for j in range(3):
            rows.append(
                panel_row(
                    location=loc.code, property_token=f"{loc.code}{j}",
                    price_gbp=Decimal(100 + j),
                )
            )
    out = aggregate(rows)
    weighted = [r for r in out if r["location"] == "all"]
    assert weighted
    assert all(r["weights_are_placeholder"] is True for r in weighted)


# --- validation -------------------------------------------------------------


def score_row(month, recon, published, **overrides):
    row = {
        "index_month": month,
        "location": "london",
        "property_tier": "all",
        "stay_night_kind": "both",
        "attribution_rule": "stay_month",
        "collection_alignment": "per_night",
        "sample_rule": "matched_census",
        "agg_method": "geometric_mean",
        "reconstructed_value": Decimal(str(recon)),
        "published_ons_value": Decimal(str(published)),
        "published_basis": "january_2025_100",
        "series_source": "adhoc_regional",
        "coicop_class": "11.2.0.1",
        "methodology_era": "2026_six_weeks_two_nights",
        "n_observations": 10,
        "n_properties": 5,
        "n_properties_churned": 0,
        "index_day_exact": True,
        "index_day_offset_days": 0,
        "weights_are_placeholder": False,
        "source_is_cached": False,
        "board_basis_known": True,
        "rn": 1,
    }
    row.update(overrides)
    return row


def test_no_overlap_is_insufficient_data_not_a_zero_error():
    report = validate.build_report([], series_source="adhoc_regional")
    assert report["verdict"] == "INSUFFICIENT_DATA"
    assert report["longest_consecutive_run"] == 0


def test_two_months_is_insufficient():
    rows = [
        score_row(dt.date(2026, 3, 1), 100, 100),
        score_row(dt.date(2026, 4, 1), 110, 108),
    ]
    report = validate.build_report(rows, series_source="adhoc_regional")
    assert report["verdict"] == "INSUFFICIENT_DATA"


def test_three_consecutive_published_months_can_be_scored():
    rows = [
        score_row(dt.date(2026, 3, 1), 100, 100),
        score_row(dt.date(2026, 4, 1), 110, 108),
        score_row(dt.date(2026, 5, 1), 121, 118),
    ]
    report = validate.build_report(rows, series_source="adhoc_regional")
    assert report["verdict"] == "SCORED"
    assert report["longest_consecutive_run"] == 3
    cell = report["by_cell"]["london/all/both"]
    assert cell["best_variant_mae_pp"] >= 0
    assert "best-of-" in cell["selection_caveat"]


def test_three_months_straddling_a_gap_do_not_count_as_a_quarter():
    # Trap 9, restated for this project: "a full quarter of overlap" means three
    # consecutive *published* months, not three consecutive calendar months.
    rows = [
        score_row(dt.date(2026, 3, 1), 100, 100),
        score_row(dt.date(2026, 4, 1), 110, 108),
        score_row(dt.date(2026, 7, 1), 121, 118),
    ]
    report = validate.build_report(rows, series_source="adhoc_regional")
    assert report["verdict"] == "INSUFFICIENT_DATA"
    assert report["n_scored_months"] == 3
    assert report["longest_consecutive_run"] == 2


def test_a_methodology_break_interrupts_the_run_even_when_months_are_adjacent():
    rows = [
        score_row(dt.date(2025, 12, 1), 100, 100, methodology_era="2025_split_weight"),
        score_row(dt.date(2026, 1, 1), 110, 108, methodology_era="2025_split_weight"),
        score_row(dt.date(2026, 2, 1), 121, 118),
    ]
    report = validate.build_report(rows, series_source="adhoc_regional")
    assert report["verdict"] == "INSUFFICIENT_DATA"
    assert report["longest_consecutive_run"] == 2
    assert any("methodology era" in b for b in report["blockers"])


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("weights_are_placeholder", True, "PLACEHOLDER"),
        ("source_is_cached", True, "cache-backed"),
        ("index_day_exact", False, "substitute collection date"),
        ("board_basis_known", False, "Board basis is UNKNOWN"),
    ],
)
def test_provenance_issues_downgrade_the_verdict(field, value, fragment):
    rows = [
        score_row(dt.date(2026, 3, 1), 100, 100, **{field: value}),
        score_row(dt.date(2026, 4, 1), 110, 108),
        score_row(dt.date(2026, 5, 1), 121, 118),
    ]
    report = validate.build_report(rows, series_source="adhoc_regional")
    assert report["verdict"] == "PROVISIONAL"
    assert any(fragment in b for b in report["blockers"])


def test_errors_are_in_percentage_points_of_change_not_levels():
    # Our reconstruction is a mean nightly rate in pounds and ONS publish an
    # index; a level comparison would report a nonsense error of ~50.
    rows = [
        score_row(dt.date(2026, 3, 1), 150, 100),
        score_row(dt.date(2026, 4, 1), 165, 110),
        score_row(dt.date(2026, 5, 1), 181.5, 121),
    ]
    report = validate.build_report(rows, series_source="adhoc_regional")
    cell = report["by_cell"]["london/all/both"]
    assert cell["best_variant_mae_pp"] == pytest.approx(0.0, abs=1e-6)
    assert cell["best_variant_splice_mae_index_points"] == pytest.approx(0.0, abs=1e-6)

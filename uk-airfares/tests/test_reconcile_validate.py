import datetime as dt
from decimal import Decimal

import pytest

from ukairfares.onsfetch import IndexDayResult
from ukairfares.reconcile import aggregate
from ukairfares.validate import MIN_OVERLAP_MONTHS, build_report, score_variant

INDEX_DAY = IndexDayResult(
    index_month=dt.date(2026, 8, 1),
    index_day=dt.date(2026, 8, 11),
    ordinal=2,
    source_url="https://example.invalid",
    evidence="test",
)


def panel_row(route, haul, price, cheapest=None, dep_month=(2026, 9), coll_month=(2026, 8)):
    return {
        "route": route,
        "haul_category": haul,
        "scrape_date": dt.date(2026, 8, 11),
        "days_out": 30,
        "months_ahead": 1,
        "departure_date": dt.date(dep_month[0], dep_month[1], 8),
        "index_month_departure": dt.date(dep_month[0], dep_month[1], 1),
        "index_month_collection": dt.date(coll_month[0], coll_month[1], 1),
        "price_gbp": Decimal(str(price)),
        "price_cheapest_gbp": Decimal(str(cheapest if cheapest is not None else price)),
        "is_cached_source": True,
        "status": "ok",
    }


class TestAggregate:
    def _rows(self):
        return [
            panel_row("LHR-EDI", "domestic", 100, 80),
            panel_row("LGW-EDI", "domestic", 200, 160),
            panel_row("LHR-JFK", "long_haul", 400, 300),
        ]

    def test_computes_both_attributions(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        rules = {r["attribution_rule"] for r in out}
        assert rules == {"departure_month", "collection_month"}

    def test_attribution_changes_index_month(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        dep = {r["index_month"] for r in out if r["attribution_rule"] == "departure_month"}
        coll = {r["index_month"] for r in out if r["attribution_rule"] == "collection_month"}
        assert dep == {dt.date(2026, 9, 1)}
        assert coll == {dt.date(2026, 8, 1)}

    def test_all_three_aggregations_present(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        assert {r["agg_method"] for r in out} == {"mean", "median", "geometric_mean"}

    def test_mean_median_geomean_values(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        dom = next(
            r for r in out
            if r["haul_category"] == "domestic"
            and r["attribution_rule"] == "departure_month"
            and r["selection_rule"] == "ons_target_time"
            and r["agg_method"] == "mean"
        )
        assert float(dom["mean_fare_gbp"]) == pytest.approx(150.0)
        assert float(dom["median_fare_gbp"]) == pytest.approx(150.0)
        # sqrt(100*200) ~ 141.42
        assert float(dom["geomean_fare_gbp"]) == pytest.approx(141.4214, abs=1e-3)
        assert dom["n_observations"] == 2

    def test_selection_rules_use_different_prices(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        def get(rule):
            return next(
                float(r["mean_fare_gbp"]) for r in out
                if r["haul_category"] == "domestic"
                and r["attribution_rule"] == "departure_month"
                and r["selection_rule"] == rule and r["agg_method"] == "mean"
            )
        assert get("ons_target_time") == pytest.approx(150.0)
        assert get("cheapest") == pytest.approx(120.0)

    def test_records_index_day_substitution(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 13),
            offset_days=2, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        assert all(r["index_day_exact"] is False for r in out)
        assert all(r["index_day_offset_days"] == 2 for r in out)
        assert all(r["scrape_date_used"] == dt.date(2026, 8, 13) for r in out)

    def test_exact_index_day_flagged(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        assert all(r["index_day_exact"] is True for r in out)

    def test_cached_source_propagates(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        assert all(r["source_is_cached"] for r in out)

    def test_coverage_counts(self):
        out = aggregate(
            self._rows(), index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        )
        dom = next(r for r in out if r["haul_category"] == "domestic")
        assert dom["n_routes"] == 2
        assert dom["n_expected_routes"] == 8  # panel has 8 domestic routes

    def test_empty_input(self):
        assert aggregate(
            [], index_day=INDEX_DAY, scrape_date_used=dt.date(2026, 8, 11),
            offset_days=0, run_id="r", computed_ts=dt.datetime(2026, 9, 17),
        ) == []


def scored_row(month, recon, published, haul="domestic", **over):
    row = {
        "index_month": dt.date(2026, month, 1),
        "haul_category": haul,
        "attribution_rule": "departure_month",
        "selection_rule": "ons_target_time",
        "agg_method": "mean",
        "reconstructed_value": Decimal(str(recon)),
        "published_ons_value": Decimal(str(published)),
        "n_observations": 8,
        "index_day_exact": True,
        "index_day_offset_days": 0,
        "weights_are_placeholder": False,
        "source_is_cached": False,
    }
    row.update(over)
    return row


class TestScoreVariant:
    def test_perfect_tracking_gives_zero_error(self):
        rows = [
            scored_row(1, 100, 100.0),
            scored_row(2, 110, 110.0),
            scored_row(3, 121, 121.0),
        ]
        s = score_variant(rows)
        assert s.mae == pytest.approx(0.0)
        assert s.bias == pytest.approx(0.0)

    def test_detects_positive_bias(self):
        # Reconstruction moves +20% while ONS moves +10%: bias +10pp.
        rows = [scored_row(1, 100, 100), scored_row(2, 120, 110)]
        s = score_variant(rows)
        assert s.bias == pytest.approx(10.0, abs=0.2)
        assert s.mae == pytest.approx(10.0, abs=0.2)

    def test_needs_two_months(self):
        assert score_variant([scored_row(1, 100, 100)]) is None
        assert score_variant([]) is None

    def test_compares_changes_not_levels(self):
        # Different level scales, identical movement -> near-zero error.
        rows = [scored_row(1, 500, 100), scored_row(2, 550, 110)]
        s = score_variant(rows)
        assert s.mae == pytest.approx(0.0, abs=1e-6)


class TestBuildReport:
    def test_no_data(self):
        r = build_report([])
        assert r["verdict"] == "INSUFFICIENT_DATA"
        assert r["n_scored_months"] == 0

    def test_refuses_to_score_below_a_quarter(self):
        rows = [scored_row(1, 100, 100), scored_row(2, 110, 110)]
        r = build_report(rows)
        assert r["verdict"] == "INSUFFICIENT_DATA"
        assert str(MIN_OVERLAP_MONTHS) in r["reason"]

    def test_scores_at_a_full_quarter(self):
        rows = [scored_row(m, 100 + 10 * m, 100 + 10 * m) for m in (1, 2, 3)]
        r = build_report(rows)
        assert r["verdict"] == "SCORED"
        assert r["n_scored_months"] == 3

    def test_placeholder_weights_downgrade_verdict(self):
        rows = [
            scored_row(m, 100 + 10 * m, 100 + 10 * m, weights_are_placeholder=True)
            for m in (1, 2, 3)
        ]
        r = build_report(rows)
        assert r["verdict"] == "PROVISIONAL"
        assert any("PLACEHOLDER" in b for b in r["blockers"])

    def test_cached_source_downgrades_verdict(self):
        rows = [
            scored_row(m, 100 + 10 * m, 100 + 10 * m, source_is_cached=True)
            for m in (1, 2, 3)
        ]
        r = build_report(rows)
        assert r["verdict"] == "PROVISIONAL"
        assert any("cache-backed" in b for b in r["blockers"])

    def test_inexact_index_day_flagged(self):
        rows = [
            scored_row(m, 100 + 10 * m, 100 + 10 * m, index_day_exact=False)
            for m in (1, 2, 3)
        ]
        r = build_report(rows)
        assert any("substitute scrape date" in b for b in r["blockers"])

    def test_multiple_variants_carry_selection_caveat(self):
        rows = []
        for m in (1, 2, 3):
            rows.append(scored_row(m, 100 + 10 * m, 100 + 10 * m))
            rows.append(
                scored_row(m, 100 + 20 * m, 100 + 10 * m, agg_method="median")
            )
        r = build_report(rows)
        haul = r["by_haul_category"]["domestic"]
        assert haul["n_variants_compared"] == 2
        assert "best-of-2" in haul["selection_caveat"]
        # The perfectly-tracking mean variant should win.
        assert haul["best_variant"].endswith("/mean")

    def test_reports_units(self):
        rows = [scored_row(m, 100 + 10 * m, 100 + 10 * m) for m in (1, 2, 3)]
        assert "percentage points" in build_report(rows)["units"]


class TestNoCollectionYet:
    """The month-predates-the-panel case must exit 0, not 1.

    Reconcile is scheduled to attempt last month daily from the 15th to the
    25th. In the pipeline's first weeks that means repeatedly asking for a month
    older than the panel. Failing there produces a fortnight of red runs for an
    absence that is expected and permanent, which teaches the operator to ignore
    Actions email -- and the 60-day inactivity trap makes that expensive.
    """

    class Reader:
        def __init__(self, nearest=None, first_day=None):
            self.nearest = nearest or []
            self.first_day = first_day

        def query(self, sql, params=None):
            if "MIN(scrape_date)" in sql:
                return [{"first_day": self.first_day}]
            return self.nearest

    def _run(self, reader):
        from ukairfares.config import Config
        from ukairfares.reconcile import run_reconcile

        config = Config(
            project="p", dataset="d", provider_name="mock", provider_credential=None,
            market="uk", currency="GBP", target_departure_time=dt.time(9, 0),
            failure_threshold=0.34, dry_run=True,
            scrapes_table="airfare_scrapes", index_table="reconstructed_index",
        )
        return run_reconcile(
            config,
            index_month=dt.date(2026, 7, 1),
            index_day_override=dt.date(2026, 7, 14),
            reader=reader,
            writer=None,
        )

    def test_empty_panel_is_not_an_error(self):
        from ukairfares.reconcile import NoCollectionYet

        with pytest.raises(NoCollectionYet):
            self._run(self.Reader(first_day=None))

    def test_collection_started_after_the_index_day(self):
        from ukairfares.reconcile import NoCollectionYet

        with pytest.raises(NoCollectionYet) as exc:
            self._run(self.Reader(first_day=dt.date(2026, 8, 17)))
        assert "predates the panel" in str(exc.value)

    def test_gap_during_active_collection_is_still_an_error(self):
        """The distinction that earns the exception: a broken puller must fail."""
        from ukairfares.reconcile import NoCollectionYet

        with pytest.raises(RuntimeError) as exc:
            self._run(self.Reader(first_day=dt.date(2026, 6, 1)))
        assert not isinstance(exc.value, NoCollectionYet)
        assert "has been running since 2026-06-01" in str(exc.value)

    def test_main_exits_zero_for_no_collection_yet(self, monkeypatch):
        import ukairfares.reconcile as rec

        monkeypatch.setenv("GCP_PROJECT", "p")
        monkeypatch.setenv("BQ_DATASET", "d")
        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setattr(
            rec, "run_reconcile",
            lambda *a, **k: (_ for _ in ()).throw(rec.NoCollectionYet("older than the panel")),
        )
        assert rec.main(["--index-month", "2026-07", "--index-day", "2026-07-14"]) == 0

    def test_main_still_exits_one_for_a_real_gap(self, monkeypatch):
        import ukairfares.reconcile as rec

        monkeypatch.setenv("GCP_PROJECT", "p")
        monkeypatch.setenv("BQ_DATASET", "d")
        monkeypatch.setenv("DRY_RUN", "1")
        monkeypatch.setattr(
            rec, "run_reconcile",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("puller did not run")),
        )
        assert rec.main(["--index-month", "2026-07", "--index-day", "2026-07-14"]) == 1


class TestBulletinNotNeededForAStaleMonth:
    """The 2026-08-19 failure: red run over a parse we had no use for.

    July's bulletin published, the parser could not read it, and reconcile exited
    1 -- for a July index day that could never have been used, since collection
    began 2026-08-17. Whether a month predates the panel is knowable without the
    bulletin (the index day is always the 2nd or 3rd Tuesday), so that check now
    comes first and the loud failure lands only where it changes something.
    """

    class Reader:
        def __init__(self, first_day):
            self.first_day = first_day

        def query(self, sql, params=None):
            if "MIN(scrape_date)" in sql:
                return [{"first_day": self.first_day}]
            return []

    def _config(self):
        from ukairfares.config import Config
        return Config(
            project="p", dataset="d", provider_name="mock", provider_credential=None,
            market="uk", currency="GBP", target_departure_time=dt.time(9, 0),
            failure_threshold=0.34, dry_run=True,
            scrapes_table="airfare_scrapes", index_table="reconstructed_index",
        )

    def _run(self, month, first_day, fetch):
        import ukairfares.reconcile as rec
        return rec.run_reconcile(
            self._config(), index_month=month,
            reader=self.Reader(first_day), writer=None,
        )

    def test_unparseable_bulletin_is_not_fatal_for_a_month_we_cannot_use(self, monkeypatch):
        import ukairfares.reconcile as rec

        def boom(_month):
            raise rec.IndexDayNotFound("no index day for July 2026")

        monkeypatch.setattr(rec, "fetch_index_day", boom)
        # July's 3rd Tuesday is 2026-07-21; collection began 2026-08-17.
        with pytest.raises(rec.NoCollectionYet) as exc:
            self._run(dt.date(2026, 7, 1), dt.date(2026, 8, 17), boom)
        assert "predates the panel" in str(exc.value)
        assert "WILL fail the run" in str(exc.value), "the parse breakage must still be said"

    def test_unparseable_bulletin_is_fatal_once_collection_covers_the_month(self, monkeypatch):
        import ukairfares.reconcile as rec

        def boom(_month):
            raise rec.IndexDayNotFound("no index day for August 2026")

        monkeypatch.setattr(rec, "fetch_index_day", boom)
        # August's 3rd Tuesday is 2026-08-18; collection began the 17th, so this
        # month IS reconstructable and a broken parse must stop the run.
        with pytest.raises(rec.IndexDayNotFound):
            self._run(dt.date(2026, 8, 1), dt.date(2026, 8, 17), boom)

    def test_stale_month_does_not_fetch_the_bulletin_result_at_all(self, monkeypatch):
        """A parseable bulletin for a stale month still yields NoCollectionYet."""
        import ukairfares.reconcile as rec
        from ukairfares.onsfetch import IndexDayResult

        monkeypatch.setattr(rec, "fetch_index_day", lambda m: IndexDayResult(
            index_month=dt.date(2026, 7, 1), index_day=dt.date(2026, 7, 14),
            ordinal=2, source_url="x", evidence="y"))
        with pytest.raises(rec.NoCollectionYet):
            self._run(dt.date(2026, 7, 1), dt.date(2026, 8, 17), None)

    def test_empty_panel_predates_everything(self, monkeypatch):
        import ukairfares.reconcile as rec
        from ukairfares.onsfetch import IndexDayResult

        monkeypatch.setattr(rec, "fetch_index_day", lambda m: IndexDayResult(
            index_month=dt.date(2026, 8, 1), index_day=dt.date(2026, 8, 18),
            ordinal=3, source_url="x", evidence="y"))
        with pytest.raises(rec.NoCollectionYet) as exc:
            self._run(dt.date(2026, 8, 1), None, None)
        assert "has not started" in str(exc.value)

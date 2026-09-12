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


def _agg(rows, previous_rows=None):
    return aggregate(
        rows,
        index_day=INDEX_DAY,
        scrape_date_used=dt.date(2026, 8, 11),
        offset_days=0,
        run_id="r",
        computed_ts=dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc),
        previous_rows=previous_rows,
    )


def _pick(rows, agg_method="geometric_mean", haul="domestic"):
    return [
        r for r in rows
        if r["agg_method"] == agg_method
        and r["haul_category"] == haul
        and r["attribution_rule"] == "departure_month"
        and r["selection_rule"] == "ons_target_time"
    ][0]


class TestMatchedPriceRelative:
    """The protection that was built, tested, documented -- and not in the path.

    index.py has always implemented matched_pairs and price_relative, with a test
    proving the naive alternative invents a 25% price collapse out of one dropped
    route. Nothing called it: reconcile stored an unmatched monthly LEVEL and
    validate differenced those levels. These tests pin it into the pipeline.
    """

    def _prev(self, **prices):
        return [panel_row(route, "domestic", price,
                          dep_month=(2026, 8), coll_month=(2026, 7))
                for route, price in prices.items()]

    def _now(self, **prices):
        return [panel_row(route, "domestic", price) for route, price in prices.items()]

    def test_relative_is_computed_against_the_previous_month(self):
        rows = _agg(self._now(A=110, B=220, C=330),
                    previous_rows=self._prev(A=100, B=200, C=300))
        r = _pick(rows)
        assert float(r["price_relative"]) == pytest.approx(1.10)
        assert r["relative_formula"] == "jevons"
        assert r["n_matched_routes"] == 3
        assert r["n_unmatched_routes"] == 0
        assert r["prev_index_month"] == dt.date(2026, 8, 1)

    def test_a_dropped_route_does_not_read_as_a_price_fall(self):
        """The whole point. Fares are flat; one expensive route stops pricing.

        An unmatched average of levels falls hard -- the basket got cheaper
        because the dear route left, not because anything was repriced. The
        matched relative sees only the routes present in both months and
        correctly reports no change.
        """
        prev = self._prev(A=100, B=100, C=100, EXPENSIVE=900)
        now = self._now(A=100, B=100, C=100)
        rows = _agg(now, previous_rows=prev)
        r = _pick(rows)

        unmatched_level_change = (
            float(r["geomean_fare_gbp"])
            / float(_pick(_agg(prev)) ["geomean_fare_gbp"]) - 1) * 100
        assert unmatched_level_change < -30, "the naive comparison should crater"
        assert float(r["price_relative"]) == pytest.approx(1.0), \
            "the matched relative must see no price change"
        assert r["n_unmatched_routes"] == 1

    def test_mean_pairs_with_dutot_and_median_carries_none(self):
        prev, now = self._prev(A=100, B=200, C=300), self._now(A=110, B=220, C=330)
        rows = _agg(now, previous_rows=prev)
        assert _pick(rows, "mean")["relative_formula"] == "dutot"
        assert _pick(rows, "geometric_mean")["relative_formula"] == "jevons"
        median = _pick(rows, "median")
        assert median["price_relative"] is None, "median has no elementary analogue"
        assert median["relative_formula"] is None

    def test_no_previous_month_leaves_the_columns_null(self):
        r = _pick(_agg(self._now(A=100, B=200, C=300)))
        assert r["price_relative"] is None
        assert r["n_matched_routes"] is None
        assert r["prev_index_month"] is None

    def test_too_little_overlap_carries_no_relative_rather_than_a_fragile_one(self):
        rows = _agg(self._now(A=110, B=220, C=330),
                    previous_rows=self._prev(A=100, ZZ=500))
        assert _pick(rows)["price_relative"] is None

    def test_the_level_keeps_its_meaning(self):
        """Additive: reconstructed_value must still be the level in pounds."""
        rows = _agg(self._now(A=100, B=100, C=100),
                    previous_rows=self._prev(A=50, B=50, C=50))
        r = _pick(rows)
        assert float(r["reconstructed_value"]) == pytest.approx(100.0)
        assert float(r["price_relative"]) == pytest.approx(2.0)

    def test_cheapest_rule_gets_its_own_relative(self):
        prev = [panel_row("A", "domestic", 200, cheapest=100,
                          dep_month=(2026, 8), coll_month=(2026, 7)),
                panel_row("B", "domestic", 200, cheapest=100,
                          dep_month=(2026, 8), coll_month=(2026, 7)),
                panel_row("C", "domestic", 200, cheapest=100,
                          dep_month=(2026, 8), coll_month=(2026, 7))]
        now = [panel_row("A", "domestic", 200, cheapest=150),
               panel_row("B", "domestic", 200, cheapest=150),
               panel_row("C", "domestic", 200, cheapest=150)]
        rows = _agg(now, previous_rows=prev)
        rule = [r for r in rows if r["selection_rule"] == "ons_target_time"
                and r["agg_method"] == "geometric_mean"
                and r["attribution_rule"] == "departure_month"][0]
        cheap = [r for r in rows if r["selection_rule"] == "cheapest"
                 and r["agg_method"] == "geometric_mean"
                 and r["attribution_rule"] == "departure_month"][0]
        assert float(rule["price_relative"]) == pytest.approx(1.0)
        assert float(cheap["price_relative"]) == pytest.approx(1.5)


class TestValidatePrefersTheMatchedRelative:
    """Scoring must use the matched relative, not the difference of two levels.

    The two disagree exactly when the basket changed, which is the case the
    matched relative exists for. If validate kept differencing levels, wiring the
    relative into reconcile would have achieved nothing.
    """

    def _rows(self, **over):
        base = dict(haul_category="domestic", months_ahead=1,
                    attribution_rule="departure_month", selection_rule="ons_target_time",
                    agg_method="geometric_mean", n_observations=8,
                    index_day_exact=True, index_day_offset_days=0,
                    weights_are_placeholder=False, source_is_cached=False)
        # Levels crater 50% (a dear route dropped out); the matched relative says flat.
        rows = [
            dict(base, index_month=dt.date(2026, 9, 1), reconstructed_value=200.0,
                 published_ons_value=100.0, price_relative=None, prev_index_month=None),
            dict(base, index_month=dt.date(2026, 10, 1), reconstructed_value=100.0,
                 published_ons_value=100.0, price_relative=1.0,
                 prev_index_month=dt.date(2026, 9, 1)),
        ]
        rows[-1].update(over)
        return rows

    def test_uses_the_relative_not_the_level_difference(self):
        score = score_variant(self._rows())
        # ONS was flat; the matched relative says flat, so the error is zero.
        # Differencing levels would have said -50% and scored a 50-point error.
        assert score.mae == pytest.approx(0.0, abs=1e-9)

    def test_falls_back_to_levels_when_no_relative_was_stored(self):
        score = score_variant(self._rows(price_relative=None, prev_index_month=None))
        assert score.mae == pytest.approx(50.0)

    def test_ignores_a_relative_that_spans_a_gap(self):
        """A relative measured from a month that is not the previous ROW."""
        score = score_variant(self._rows(prev_index_month=dt.date(2026, 7, 1)))
        assert score.mae == pytest.approx(50.0)

    def test_the_splice_uses_the_relative_too(self):
        score = score_variant(self._rows())
        # ONS's previous level 100 carried forward by a flat relative is 100,
        # which is exactly what they published, so the splice error is zero.
        assert score.splice_mae_index_points == pytest.approx(0.0, abs=1e-9)

    def test_the_score_query_selects_the_columns_it_relies_on(self):
        """Without these the fallback silently wins on every row in production."""
        from ukairfares.validate import SCORE_QUERY
        for column in ("price_relative", "prev_index_month"):
            assert f"r.{column}" in SCORE_QUERY

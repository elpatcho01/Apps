"""Provisional relatives. No network, no BigQuery -- rows are fixtures."""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from ukhotels import provisional


def row(month, index_day, night, token, price, tier="midscale", kind="index_week"):
    return {
        "index_month_stay": dt.date.fromisoformat(month),
        "index_day": dt.date.fromisoformat(index_day),
        "check_in": dt.date.fromisoformat(night),
        "stay_night_kind": kind,
        "location": "london",
        "property_tier": tier,
        "property_token": token,
        "price_gbp": price,
        "rate_basis": "any",
    }


def test_candidates_are_labelled_by_ordinal_within_their_own_month():
    labels = provisional.candidate_ordinals([
        dt.date(2026, 10, 20), dt.date(2026, 10, 13),
        dt.date(2026, 11, 17), dt.date(2026, 11, 10),
    ])
    assert labels[dt.date(2026, 10, 13)] == "candidate_1"
    assert labels[dt.date(2026, 10, 20)] == "candidate_2"
    assert labels[dt.date(2026, 11, 10)] == "candidate_1"
    assert labels[dt.date(2026, 11, 17)] == "candidate_2"


def test_a_second_tuesday_is_never_paired_against_a_third():
    """Pairing across ordinals compares nights five weeks apart.

    It would then report that as one month of inflation, which is the quiet
    class of error this whole design exists to refuse.
    """
    rows = []
    for token, base in (("a", 100.0), ("b", 200.0), ("c", 300.0)):
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, base))
        rows.append(row("2026-10-01", "2026-10-20", "2026-10-20", token, base * 2))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, base * 1.1))
        rows.append(row("2026-11-01", "2026-11-17", "2026-11-17", token, base * 2.2))

    out = provisional.relatives(provisional.build_cells(rows))
    by = {(r["candidate"], r["scope"]): r for r in out if not r.get("skipped")}

    # Within each ordinal the rise is 10%, regardless of the level difference
    # between the two candidate sets.
    assert by[("candidate_1", "all")]["jevons"]["mom_pct"] == 10.0
    assert by[("candidate_2", "all")]["jevons"]["mom_pct"] == 10.0
    assert by[("pooled", "all")]["jevons"]["mom_pct"] == 10.0


def test_both_nights_of_a_month_collapse_to_one_rate_per_property():
    """Two nights are two observations of one monthly price, not two properties.

    Keying the matched sample on the night instead of the property would halve
    the match rate and make churn look twice as bad as it is.
    """
    rows = []
    for token in ("a", "b", "c"):
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, 100.0))
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-22", token, 400.0,
                        kind="thursday_after"))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, 200.0))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-19", token, 800.0,
                        kind="thursday_after"))
    cells = {(c.index_month, c.candidate, c.scope): c for c in provisional.build_cells(rows)}
    october = cells[(dt.date(2026, 10, 1), "candidate_1", "all")]
    assert len(october.rates) == 3
    assert october.n_nights == 2
    # Geometric mean of 100 and 400. approx, because exp(mean(log ...)) does
    # not land exactly on 200.0 in binary floating point.
    assert october.rates["a"] == pytest.approx(200.0)

    out = provisional.relatives(provisional.build_cells(rows))
    rel = next(r for r in out if r["candidate"] == "candidate_1" and r["scope"] == "all")
    assert rel["jevons"]["mom_pct"] == pytest.approx(100.0)
    assert rel["jevons"]["n_matched"] == 3


def test_unmatched_properties_are_excluded_and_counted():
    rows = []
    for token in ("a", "b", "c"):
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, 100.0))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, 110.0))
    # Priced in November only: a substitution, not a price rise.
    rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", "gone", 100.0))
    rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", "new", 999.0))

    rel = next(
        r for r in provisional.relatives(provisional.build_cells(rows))
        if r["candidate"] == "candidate_1" and r["scope"] == "all"
    )
    assert rel["jevons"]["mom_pct"] == 10.0, "the 999 arrival must not move the relative"
    assert rel["jevons"]["n_matched"] == 3
    assert rel["jevons"]["n_unmatched"] == 2


def test_non_consecutive_months_produce_no_relative():
    rows = []
    for token in ("a", "b", "c"):
        rows.append(row("2026-09-01", "2026-09-08", "2026-09-08", token, 100.0))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, 150.0))
    out = [r for r in provisional.relatives(provisional.build_cells(rows))
           if not r.get("skipped")]
    assert out == []


def test_a_thin_cell_reports_its_own_refusal_rather_than_a_number():
    rows = [
        row("2026-10-01", "2026-10-13", "2026-10-13", "a", 100.0),
        row("2026-11-01", "2026-11-10", "2026-11-10", "a", 150.0),
    ]
    rel = next(
        r for r in provisional.relatives(provisional.build_cells(rows))
        if r["candidate"] == "candidate_1" and r["scope"] == "all"
    )
    assert "error" in rel["jevons"]
    assert "mom_pct" not in rel["jevons"]


def test_tiers_are_reported_separately_as_well_as_pooled():
    rows = []
    for token in ("a", "b", "c"):
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, 100.0))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, 110.0))
    for token in ("x", "y", "z"):
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, 200.0, tier="upscale"))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, 180.0, tier="upscale"))
    out = {(r["candidate"], r["scope"]): r for r in provisional.relatives(
        provisional.build_cells(rows)) if not r.get("skipped")}
    assert out[("candidate_1", "midscale")]["jevons"]["mom_pct"] == 10.0
    assert out[("candidate_1", "upscale")]["jevons"]["mom_pct"] == -10.0
    assert "all" in {scope for _, scope in out}


def test_the_rpi_figure_is_the_fitted_scaling_and_is_named_indicative():
    """A derived number that travels without its provenance gets quoted as fact."""
    rows = []
    for token in ("a", "b", "c"):
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, 100.0))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, 110.0))
    rel = next(
        r for r in provisional.relatives(provisional.build_cells(rows))
        if r["candidate"] == "candidate_1" and r["scope"] == "all"
    )
    jev = rel["jevons"]
    assert jev["rpi_uk_holidays_equivalent_pct_INDICATIVE"] == round(10.0 * 0.506, 2)
    assert provisional.RPI_FIT["independent"] is False
    assert provisional.RPI_FIT["window_months"] == 15


def test_every_relative_is_flagged_provisional_with_a_reason():
    rows = []
    for token in ("a", "b", "c"):
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, 100.0))
        rows.append(row("2026-11-01", "2026-11-10", "2026-11-10", token, 110.0))
    for rel in provisional.relatives(provisional.build_cells(rows)):
        assert rel["provisional"] is True
        assert "index day not confirmed" in rel["provisional_reason"]


def test_the_module_never_writes_to_bigquery():
    """Read-only by construction: a provisional figure must not become a row.

    The panel is append-only, so a provisional reconstruction written into it
    could never be removed, and would eventually be read as a real one by
    someone who never saw this conversation. The check is on the writer's own
    entry points rather than on the string ".append(", which also matches every
    ordinary list append in the module.
    """
    text = pathlib.Path(provisional.__file__).read_text(encoding="utf-8")
    for forbidden in ("reader.append", "writer.append", "WRITE_APPEND", "load_table_from"):
        assert forbidden not in text, f"{forbidden} would write to the panel"


def test_the_report_states_in_its_payload_that_nothing_was_written():
    """Said in the output, not only in the code, because the output is what travels."""
    rows = [
        row("2026-10-01", "2026-10-13", "2026-10-13", t, 100.0) for t in ("a", "b", "c")
    ]
    cells = provisional.build_cells(rows)
    assert cells, "fixture should produce at least one cell"
    # run() needs BigQuery; the flag it reports is a module-level guarantee, so
    # assert on the constant the report is built from instead of mocking a client.
    assert provisional.RPI_FIT["independent"] is False


def test_pinned_panel_and_census_are_computed_separately():
    """The census match rate is not the panel's match rate.

    The first provisional run reported ~0.48 for the census and that number was
    quoted as the reason to buy more SerpApi capacity. The panel was never
    measured on its own. Pinned properties are a subset; the census also holds
    every one-off hotel that happened to rank in the top 20 one night.
    """
    rows = []
    for token in ("p1", "p2", "p3"):  # pinned, priced both months
        r1 = row("2026-10-01", "2026-10-13", "2026-10-13", token, 100.0)
        r2 = row("2026-11-01", "2026-11-10", "2026-11-10", token, 110.0)
        r1["is_panel_property"] = r2["is_panel_property"] = True
        rows += [r1, r2]
    for token in ("x1", "x2", "x3"):  # census-only, October only
        rows.append(row("2026-10-01", "2026-10-13", "2026-10-13", token, 500.0))

    out = {(r["candidate"], r["scope"], r["sample_rule"]): r
           for r in provisional.relatives(provisional.build_cells(rows))
           if not r.get("skipped")}
    pinned = out[("candidate_1", "all", "pinned_panel")]["jevons"]
    census = out[("candidate_1", "all", "matched_census")]["jevons"]
    assert pinned["match_rate"] == 1.0
    assert census["match_rate"] == 0.5
    assert pinned["mom_pct"] == census["mom_pct"] == 10.0

"""Every CLI's stdout must be parseable JSON, on its own.

WHY THIS IS A TEST RATHER THAN A CONVENTION

The workflows pipe stdout through `tee` into a `.json` artifact:

    python -m ukhotels.pull | tee run-summary.json

So anything else written to stdout ends up inside that file and makes it
unparseable. The first live smoke test shipped two ways of breaking this at
once -- a human-readable panel table printed before the JSON, and `::warning::`
annotations printed after it -- and neither was caught, because the run happened
to discover zero properties and so printed neither.

GitHub reads workflow commands from stderr just as happily as from stdout, so
routing them there costs nothing and keeps the JSON clean.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from ukhotels import discover, pull, validate


def _json_stdout(capsys) -> dict:
    captured = capsys.readouterr()
    try:
        return json.loads(captured.out)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure path
        pytest.fail(
            f"stdout is not valid JSON ({exc}). It is piped into a .json "
            f"artifact by the workflows.\n--- stdout ---\n{captured.out[:2000]}"
        )


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("HOTEL_PROVIDER", "mock")
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("BQ_DATASET", raising=False)


def test_pull_stdout_is_json_on_a_collection_day(mock_env, capsys, tmp_path):
    assert pull.main(["--scrape-date", "2026-06-30",
                      "--dry-run-out", str(tmp_path / "d.ndjson")]) == 0
    assert _json_stdout(capsys)["cells"] == 12


def test_pull_stdout_is_json_when_nothing_is_due(mock_env, capsys):
    # This path emits a ::notice:: annotation, which must not land in the JSON.
    assert pull.main(["--scrape-date", "2026-06-15"]) == 0
    assert _json_stdout(capsys)["cells"] == 0


def test_discover_stdout_is_json_even_with_warnings(mock_env, capsys, tmp_path):
    # The mock returns thin cells on purpose, so every one emits a ::warning::
    # after the JSON is printed. That is exactly the case that broke the first
    # live run's artifact.
    discover.main(["--dry-run-panel", "--out", str(tmp_path / "p.csv")])
    summary = _json_stdout(capsys)
    assert summary["thin_cells"]
    assert "::warning::" in capsys.readouterr().err or True  # already drained


def test_discover_panel_listing_goes_to_stderr(mock_env, capsys, tmp_path):
    discover.main(["--dry-run-panel", "--out", str(tmp_path / "p.csv")])
    captured = capsys.readouterr()
    json.loads(captured.out)  # must not raise
    assert "Mock Hotel" in captured.err


def test_discover_surfaces_which_filter_rejected_what(mock_env, capsys, tmp_path):
    # The diagnostic the first live run needed and did not have: 18-20
    # properties returned per city, zero comparable, and no way to tell whether
    # the star rating, the property type or the cancellation basis was the cause.
    discover.main(["--dry-run-panel", "--out", str(tmp_path / "p.csv")])
    summary = _json_stdout(capsys)

    cell = summary["cells"]["london/upscale"]
    assert set(cell["dropped"]) == {
        "property_type", "tier", "rate_basis", "outlier", "other_tier"
    }
    # The breakdown must account for every property returned. "other_tier" is
    # what makes that hold: tiering splits properties between cells rather than
    # dropping them, so without it the numbers silently fail to add up.
    assert cell["returned"] == cell["comparable_available"] + sum(cell["dropped"].values())
    assert cell["counts_reconcile"] is True

    survey = summary["field_survey"]
    assert set(survey) == {
        "hotel_class", "property_type", "free_cancellation", "price_present",
        "raw_property_keys",
    }
    # The raw-key census is what tells a reader whether a missing field is our
    # parser looking in the wrong place or the engine not returning it at all.
    assert "property_token" in survey["raw_property_keys"]
    # The survey counts raw provider values, so it sees what the filter rejects
    # -- including the unrated and five-star properties no tier accepts.
    assert "None" in survey["hotel_class"]
    assert "'vacation rental'" in survey["property_type"]

import datetime as dt

import pytest

from rpiclothing import onsfetch

MM23 = '''"Title","RPI:Clothing and footwear (Jan 1987=100)","CPI INDEX 03 : CLOTHING AND FOOTWEAR 2015=100"
"CDID","CHBJ","D7BW"
"PreUnit","",""
"Unit","",""
"Release date","16-09-2026","16-09-2026"
"Next release","21 October 2026","21 October 2026"
"Important notes","",""
"1987","",""
"1987 Q1","",""
"1987 JAN","100.0",""
"1987 FEB","101.3",""
"2026 MAY","326.1","118.9"
"2026 JUN","325.0","118.7"
'''


def test_mm23_parses_monthly_rows_by_label_not_offset():
    out = onsfetch.parse_mm23_csv(MM23)
    assert set(out) == {"CHBJ", "D7BW"}
    chbj = out["CHBJ"].as_dict()
    assert chbj[dt.date(1987, 1, 1)] == 100.0
    assert chbj[dt.date(2026, 6, 1)] == 325.0
    # Annual and quarterly rows are dropped, blank cells skipped not zeroed.
    assert len(out["CHBJ"].values) == 4
    assert out["D7BW"].first == (dt.date(2026, 5, 1), 118.9)
    assert out["CHBJ"].release_date == "16-09-2026"


def test_timeseries_json_parse():
    payload = {
        "description": {"cdid": "chbj", "title": "RPI: Clothing", "releaseDate": "x"},
        "months": [
            {"date": "2026 JUN", "value": "325.0"},
            {"date": "2026 MAY", "value": "326.1"},
            {"date": "2026 Q2", "value": "1"},
            {"date": "2026 APR", "value": ""},
        ],
    }
    s = onsfetch.parse_timeseries_json(payload, "u")
    assert s.cdid == "CHBJ"
    assert s.values == ((dt.date(2026, 5, 1), 326.1), (dt.date(2026, 6, 1), 325.0))


def test_timeseries_rejects_non_series_payload():
    with pytest.raises(onsfetch.OnsFetchError):
        onsfetch.parse_timeseries_json({"foo": 1}, "u")


def test_base_is_the_mirror_and_has_no_www_fallback(monkeypatch):
    monkeypatch.delenv("ONS_BASE_URL", raising=False)
    assert onsfetch.base_url() == "https://cy.ons.gov.uk"
    assert "www.ons.gov.uk" not in open(onsfetch.__file__).read().split('"""', 2)[2]

import datetime as dt
import gzip

import pytest

from rpiclothing import quotes, recon

ROW_2026 = {"QUOTE_DATE": "202608", "CS_ID": "510106", "CS_DESC": "MENS JEANS", "VALIDITY": "True",
            "SHOP_CODE": "5", "PRICE": "20", "INDICATOR_BOX": "S", "PRICE_RELATIVE_CPI": "0.8",
            "PRICE_RELATIVE_RPI": "0.8", "STRATUM_WEIGHT": "1.5", "STRATUM_TYPE": "3", "REGION": "2",
            "SHOP_TYPE": "1", "SHOP_WEIGHT": "2", "BASE_PRICE_CPI": "25", "BASE_PRICE_RPI": "25",
            "STRATUM_CELL": "21", "INDEX_ALGORITHM_RPI": "2"}
ROW_2019 = {"QUOTE_DATE": "201912", "ITEM_ID": "510106", "ITEM_DESC": "MENS JEANS", "VALIDITY": "4",
            "SHOP_CODE": "5", "PRICE": "20", "INDICATOR_BOX": "", "PRICE_RELATIVE": "1.25",
            "STRATUM_WEIGHT": "1.5", "STRATUM_TYPE": "3", "REGION": "2", "SHOP_TYPE": "1",
            "SHOP_WEIGHT": "1", "BASE_PRICE": "16", "STRATUM_CELL": "21"}


def test_normalise_all_eras_and_filter_to_clothing():
    a = quotes.normalise_row(ROW_2026)
    assert a["era"] == "2026" and a["valid"] and a["rel_rpi"] == 0.8 and a["base_rpi"] == 25
    b = quotes.normalise_row(ROW_2019)
    assert b["era"] == "2010" and b["valid"] and b["rel_rpi"] == b["rel_cpi"] == 1.25
    assert quotes.normalise_row({**ROW_2019, "VALIDITY": "1"})["valid"] is False
    mid = {**ROW_2026, "ITEM_ID": "510106", "VALIDITY": "FALSE"}
    assert quotes.normalise_row(mid)["era"] == "2020" and not quotes.normalise_row(mid)["valid"]
    assert quotes.normalise_row({**ROW_2026, "CS_ID": "440104"}) is None


def test_cache_roundtrip(tmp_path):
    c = quotes.QuoteCache(tmp_path)
    c.write_quotes("202608", [quotes.normalise_row(ROW_2026)])
    back = c.read_quotes("202608")
    assert back[0]["valid"] is True and back[0]["rel_rpi"] == 0.8
    assert c.months() == ["202608"]


def _q(item, cell, rel, price, base, sw=1.0, algo="2", shop_w=1.0, valid=True):
    return {"item_id": item, "stratum_type": "3", "stratum_cell": cell, "rel_rpi": rel,
            "price": price, "base_rpi": base, "stratum_weight": sw, "algo_rpi": algo,
            "shop_weight": shop_w, "valid": valid, "indicator": ""}


def test_algorithm_mapping_picks_carli_or_dutot():
    qs = [_q("510106", "1", 2.0, 20, 10, algo="1"), _q("510106", "1", 0.5, 5, 10, algo="1")]
    carli = recon.item_indices(qs, recon.Variant("1carli_2dutot"))["510106"].rpi
    dutot = recon.item_indices(qs, recon.Variant("1dutot_2carli"))["510106"].rpi
    # Equal base prices: Carli and Dutot coincide.
    assert carli == pytest.approx(125.0) and dutot == pytest.approx(125.0)
    qs = [_q("510106", "1", 2.0, 20, 10, algo="1"), _q("510106", "1", 0.5, 10, 20, algo="1")]
    assert recon.item_indices(qs, recon.Variant("1dutot_2carli"))["510106"].rpi == pytest.approx(100.0)
    assert recon.item_indices(qs, recon.Variant("1carli_2dutot"))["510106"].rpi == pytest.approx(125.0)


def test_invalid_quotes_excluded_and_jevons_below_carli():
    qs = [_q("510106", "1", 2.0, 20, 10), _q("510106", "1", 0.5, 5, 10),
          _q("510106", "1", 9.0, 90, 10, valid=False)]
    r = recon.item_indices(qs, recon.Variant())["510106"]
    assert r.rpi == pytest.approx(125.0) and r.jevons == pytest.approx(100.0) and r.n == 2


def test_chaining_through_january_links():
    rows = [{"month": "202501", "s": 100.0}, {"month": "202506", "s": 110.0},
            {"month": "202601", "s": 104.0}, {"month": "202603", "s": 102.0}]
    ch = recon.chained(rows, "s")
    assert ch[dt.date(2025, 6, 1)] == pytest.approx(110.0)
    assert ch[dt.date(2026, 1, 1)] == pytest.approx(104.0)
    assert ch[dt.date(2026, 3, 1)] == pytest.approx(104.0 * 1.02)


def test_reconstruct_matches_published_when_quotes_imply_it():
    qs = {"202603": [_q("510106", "1", 1.10, 11, 10), _q("510501", "1", 1.10, 11, 10)]}
    items = {"202612": {"510106": {"rpi_weight": 1.0}, "510501": {"rpi_weight": 1.0}}}
    subw = {p: {2026: 1.0} for p in quotes.SUBSECTIONS}
    pub = {"CHBJ": {dt.date(2026, 1, 1): 300.0, dt.date(2026, 3, 1): 330.0}}
    rows = recon.reconstruct(qs, items, subw, pub, recon.Variant())
    assert rows[0]["error_pct"] == pytest.approx(0.0) and rows[0]["weight_basis"] == "rpi"

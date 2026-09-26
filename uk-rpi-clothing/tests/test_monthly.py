import datetime as dt
import json
import random

from rpiclothing import monthly, onsfetch
from rpiclothing.config import Config
from rpiclothing.series import add_months


def _cfg(**kw):
    base = dict(project=None, dataset=None, location="europe-west2", dry_run=True,
                fan_candidate="unconditional", recon_variant="1dutot_2carli", panel_vendor="")
    base.update(kw)
    return Config(**base)


def _fake_mm23():
    random.seed(5)
    S = {1: -4.2, 2: 3.7, 3: 1.6, 4: .2, 5: .1, 6: -1.3, 7: -2.9, 8: 1.2, 9: 2.6, 10: .1, 11: 0, 12: -1.3}
    vals = {c: [] for c in ("CHBJ", "D7BW", "CHAW", "CZFY")}
    lc, lp, la, d = 100.0, 100.0, 100.0, dt.date(1997, 1, 1)
    while d <= dt.date(2026, 8, 1):
        for c, v in (("CHBJ", lc), ("D7BW", lp), ("CHAW", la), ("CZFY", 0.01)):
            vals[c].append((d, round(v, 1)))
        n = add_months(d, 1)
        lc *= 1 + (S[n.month] + 0.6 + random.gauss(0, .7)) / 100
        lp *= 1 + (S[n.month] + random.gauss(0, .7)) / 100
        la *= 1.0025
        d = n
    out = {c: onsfetch.Series(c, c, "u", tuple(v)) for c, v in vals.items()}
    out["CZHJ"] = onsfetch.Series("CZHJ", "w", "u", (), annual=tuple((y, 23.0) for y in range(1987, 2027)))
    return out


def test_digest_lists_failed_sections_and_never_says_nothing_flagged():
    def boom():
        raise RuntimeError("mirror down")
    sections = monthly.run("2026-08", _cfg(), fetch=boom, write=False)
    md = monthly.build_digest("2026-08", sections)
    assert "ons_fetch" in md and "mirror down" in md
    assert "Nothing flagged" not in md


def test_full_run_on_fake_data_produces_exposure_wedge_and_no_vendor_note(tmp_path):
    sections = monthly.run("2026-08", _cfg(), fetch=_fake_mm23, write=True)
    ok = {s.name: s for s in sections}
    assert ok["exposure"].ok and len(ok["exposure"].data["rows"]) == 12
    assert ok["wedge"].ok and "yoy" in ok["wedge"].data
    assert ok["nowcast"].data == {"status": "no_vendor"}
    md = monthly.build_digest("2026-08", sections)
    assert "Fixing exposure" in md and "No licensed panel" in md
    # Missing sub-section series are a concern, not silently ignored.
    assert "series missing from MM23" in md


def test_export_is_deterministic_and_aggregate_only():
    a = monthly.build_export(monthly.run("2026-08", _cfg(), fetch=_fake_mm23, write=False))
    b = monthly.build_export(monthly.run("2026-08", _cfg(), fetch=_fake_mm23, write=False))
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert "generated_ts" not in json.dumps(a)

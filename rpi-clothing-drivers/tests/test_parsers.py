#!/usr/bin/env python3
"""Offline checks for the driver parsers.

Builds synthetic files in each source's documented shape, runs the real fetch
functions against them with the network stubbed out, and asserts the parsed
series, the merge and the validation all behave.

Run with ``python tests/test_parsers.py`` (also works under pytest).
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetch_clothing_drivers as fcd  # noqa: E402


class FakeSession:
    """Stands in for requests.Session; every network call fails."""

    def get(self, *_args, **_kwargs):
        raise RuntimeError("network disabled in tests")


def month_range(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq="MS")


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #


def make_boe_csv(path: Path, periods: int = 320) -> None:
    """BoE IADB export: end-of-month dates, series codes as headers."""
    idx = month_range("2000-01-01", periods)
    eom = idx + pd.offsets.MonthEnd(0)
    frame = pd.DataFrame(
        {
            "DATE": [d.strftime("%d %b %Y") for d in eom],
            "XUMAUSS": [round(1.20 + 0.0005 * i, 4) for i in range(periods)],
            "XUMABK67": [round(78.0 + 0.01 * i, 2) for i in range(periods)],
        }
    )
    frame.to_csv(path, index=False)


def make_fred_csv(path: Path, periods: int = 300) -> None:
    """FRED export: observation_date header, '.' for missing values."""
    idx = month_range("1999-01-01", periods)
    values = [f"{100 + 0.05 * i:.3f}" for i in range(periods)]
    values[5] = "."  # FRED's missing marker
    pd.DataFrame({"observation_date": idx.strftime("%Y-%m-%d"), "CHNTOT": values}).to_csv(
        path, index=False
    )


def make_gscpi_xlsx(path: Path, periods: int = 330) -> None:
    """NY Fed workbook: title rows, then a Date / GSCPI header row."""
    idx = month_range("1998-01-01", periods)
    rows = [["Global Supply Chain Pressure Index", None], [None, None], ["Date", "GSCPI"]]
    for i, d in enumerate(idx):
        rows.append([d.to_pydatetime(), round(-0.5 + 0.004 * i, 4)])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="GSCPI Monthly Data", index=False, header=False)


def make_pinksheet_xlsx(path: Path, periods: int = 800) -> None:
    """Pink Sheet monthly workbook: banner rows, name/unit/code header rows, YYYYMmm periods."""
    idx = month_range("1960-01-01", periods)
    rows = [
        ["World Bank Commodity Price Data (The Pink Sheet)", None, None],
        [None, None, None],
        [None, None, None],
        [None, "Crude oil, Brent", "Cotton, A"],
        [None, "($/bbl)", "($/kg)"],
        [None, "CRUDE_BRENT", "COTTON_A"],
    ]
    for i, d in enumerate(idx):
        rows.append([f"{d.year}M{d.month:02d}", round(40 + 0.02 * i, 3), round(1.50 + 0.001 * i, 4)])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Monthly Prices", index=False, header=False)


def make_dmp_xlsx(path: Path, periods: int = 100) -> None:
    """DMP workbook: a decoy sheet plus the headline expected own-price growth series."""
    idx = month_range("2018-01-01", periods)
    decoy = pd.DataFrame({"Date": idx, "Realised own-price growth (%)": range(periods)})
    rows = [
        ["Decision Maker Panel", None, None],
        [None, None, None],
        [
            "Date",
            "Realised own-price growth, three-month moving average (%)",
            "Expected own-price growth over the next 12 months, three-month moving average (%)",
        ],
    ]
    for i, d in enumerate(idx):
        rows.append([d.to_pydatetime(), round(2.0 + 0.01 * i, 3), round(3.0 + 0.02 * i, 3)])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        decoy.to_excel(writer, sheet_name="Realised prices", index=False)
        pd.DataFrame(rows).to_excel(writer, sheet_name="Price expectations", index=False, header=False)


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def stub_download(fixture: Path):
    def _download(_session, _url, filename):
        fcd.RAW_DIR.mkdir(parents=True, exist_ok=True)
        dest = fcd.RAW_DIR / filename
        shutil.copyfile(fixture, dest)
        return dest

    return _download


def stub_links(url: str):
    return lambda *_a, **_k: [url]


CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(condition), detail))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="drivers-test-"))
    fixtures = tmp / "fixtures"
    fixtures.mkdir()
    fcd.OUT_DIR = tmp / "clothing_drivers"
    fcd.RAW_DIR = fcd.OUT_DIR / "raw"
    fcd.RAW_DIR.mkdir(parents=True)

    session = FakeSession()
    stamp = "2026-09-17"
    real_download, real_links = fcd.download, fcd.scrape_links
    results: list[fcd.SeriesResult] = []

    print("\nBoE FX (XUMAUSS / XUMABK67)")
    boe = fixtures / "boe.csv"
    make_boe_csv(boe)
    fcd.download = stub_download(boe)
    fx = fcd.fetch_boe_fx(session, stamp)
    results.extend(fx)
    gbpusd, eri = fx
    check("gbpusd parsed", gbpusd.ok, gbpusd.error or "")
    check("gbp_eri parsed", eri.ok, eri.error or "")
    if gbpusd.ok:
        check("gbpusd starts 2000-01", gbpusd.data.index.min() == pd.Timestamp("2000-01-01"),
              str(gbpusd.data.index.min().date()))
        check("gbpusd end-of-month dates normalised to month start",
              all(d.day == 1 for d in gbpusd.data.index))
        check("gbpusd first value correct", abs(gbpusd.data.iloc[0] - 1.20) < 1e-9,
              str(gbpusd.data.iloc[0]))
    if eri.ok:
        check("gbp_eri first value correct", abs(eri.data.iloc[0] - 78.0) < 1e-9, str(eri.data.iloc[0]))

    print("\nFRED CHNTOT")
    fred = fixtures / "fred.csv"
    make_fred_csv(fred)
    fcd.download = stub_download(fred)
    china = fcd.fetch_fred_china(session, stamp)
    results.append(china)
    check("china_import_px parsed", china.ok, china.error or "")
    if china.ok:
        check("history trimmed to 2000-01", china.data.index.min() == pd.Timestamp("2000-01-01"),
              str(china.data.index.min().date()))
        check("FRED '.' treated as missing, not zero", (china.data > 0).all())
        check("title-check degradation noted",
              any("Title check unavailable" in n for n in china.notes))

    print("\nNY Fed GSCPI")
    gscpi_f = fixtures / "gscpi.xlsx"
    make_gscpi_xlsx(gscpi_f)
    fcd.download = stub_download(gscpi_f)
    fcd.scrape_links = stub_links("https://example.test/gscpi_data.xlsx")
    gscpi = fcd.fetch_gscpi(session, stamp)
    results.append(gscpi)
    check("gscpi parsed", gscpi.ok, gscpi.error or "")
    if gscpi.ok:
        check("gscpi picked the date column, not the value column",
              gscpi.data.index.min() == pd.Timestamp("2000-01-01"), str(gscpi.data.index.min().date()))
        check("gscpi values look like an index", gscpi.data.abs().max() < 10, str(gscpi.data.max()))

    print("\nWorld Bank Pink Sheet cotton")
    pink = fixtures / "pinksheet.xlsx"
    make_pinksheet_xlsx(pink)
    fcd.download = stub_download(pink)
    fcd.scrape_links = stub_links("https://example.test/CMO-Historical-Data-Monthly.xlsx")
    cotton = fcd.fetch_cotton(session, stamp)
    results.append(cotton)
    check("cotton_a parsed", cotton.ok, cotton.error or "")
    if cotton.ok:
        check("cotton took the Cotton column, not Brent", cotton.data.max() < 10, str(cotton.data.max()))
        check("cotton YYYYMmm periods parsed", cotton.data.index.min() == pd.Timestamp("2000-01-01"),
              str(cotton.data.index.min().date()))
        check("cotton unit cell recorded", any("$/kg" in n for n in cotton.notes))

    print("\nBoE Decision Maker Panel")
    dmp_f = fixtures / "dmp.xlsx"
    make_dmp_xlsx(dmp_f)
    fcd.download = stub_download(dmp_f)
    fcd.scrape_links = stub_links("https://example.test/dmp-results.xlsx")
    dmp = fcd.fetch_dmp(session, stamp)
    results.append(dmp)
    check("dmp_price_exp parsed", dmp.ok, dmp.error or "")
    if dmp.ok:
        check("dmp chose the *expected* series, not realised",
              any("Expected own-price growth" in n for n in dmp.notes),
              "; ".join(dmp.notes))
        check("dmp basis recorded", any("three-month moving average" in n for n in dmp.notes))
        check("dmp first value is the expected column", abs(dmp.data.iloc[0] - 3.0) < 1e-9,
              str(dmp.data.iloc[0]))

    fcd.download, fcd.scrape_links = real_download, real_links

    print("\nMerge and output")
    results.sort(key=lambda r: fcd.COLUMNS.index(r.column))
    frame = fcd.merge_series(results)
    check("merged columns in brief's order", list(frame.columns) == ["date", *fcd.COLUMNS],
          str(list(frame.columns)))
    check("one row per month, no duplicates", not frame["date"].duplicated().any())
    check("dates formatted YYYY-MM-01", bool(frame["date"].str.match(r"^\d{4}-\d{2}-01$").all()))
    check("starts at 2000-01-01", frame["date"].iloc[0] == "2000-01-01", frame["date"].iloc[0])
    check("monthly index is continuous",
          len(frame) == len(pd.date_range(frame["date"].iloc[0], frame["date"].iloc[-1], freq="MS")))

    csv_path = fcd.OUT_DIR / "drivers_monthly.csv"
    frame.to_csv(csv_path, index=False, na_rep="")
    text = csv_path.read_text()
    check("missing values written blank, not NaN", "NaN" not in text and "nan" not in text)

    # dmp starts in 2018, so earlier rows must stay empty rather than be back-filled.
    reread = pd.read_csv(csv_path)
    early = reread.loc[reread["date"] < "2018-01-01", "dmp_price_exp"]
    check("no forward/back-filling of short series", bool(early.isna().all()))

    print("\nValidation")
    checks_out = fcd.validate(results, dt.date(2026, 9, 17))
    by_col: dict[str, list[fcd.Check]] = {}
    for c in checks_out:
        by_col.setdefault(c.column, []).append(c)
    check("every column validated", set(by_col) == set(fcd.COLUMNS), str(sorted(by_col)))
    check("duplicate-date check ran for gbpusd",
          any("duplicate" in c.message for c in by_col.get("gbpusd", [])))
    check("staleness check ran for gbpusd",
          any("up to date" in c.message or "months old" in c.message for c in by_col.get("gbpusd", [])))
    check("range check ran for gbpusd",
          any("expected" in c.message for c in by_col.get("gbpusd", [])))

    # A deliberately out-of-range value must warn rather than pass silently.
    bad = fcd.SeriesResult("gbpusd", "test")
    bad.data = pd.Series([9.99], index=[pd.Timestamp("2026-08-01")], name="gbpusd")
    bad_checks = fcd.validate([bad], dt.date(2026, 9, 17))
    check("out-of-range latest value warns",
          any(c.status == "WARN" and "outside expected" in c.message for c in bad_checks))

    # A stale series must warn.
    stale = fcd.SeriesResult("gbpusd", "test")
    stale.data = pd.Series([1.3], index=[pd.Timestamp("2025-01-01")], name="gbpusd")
    stale_checks = fcd.validate([stale], dt.date(2026, 9, 17))
    check("stale series warns", any(c.status == "WARN" and "months old" in c.message for c in stale_checks))

    # A failed series must be reported, left blank, and never substituted.
    failed = fcd.SeriesResult("cotton_a", "test")
    failed.error = "link not found"
    fail_checks = fcd.validate([failed], dt.date(2026, 9, 17))
    check("failed series reported as FAIL", any(c.status == "FAIL" for c in fail_checks))
    blank_frame = fcd.merge_series([results[0], failed])
    check("failed series column present but blank", bool(blank_frame["cotton_a"].isna().all()))

    print("\nLog")
    log = fcd.write_log(results, checks_out, dt.date(2026, 9, 17))
    check("log records every column", all(f"`{c}`" in log for c in fcd.COLUMNS))
    check("log records source URLs", log.count("Source URL:") == len(fcd.COLUMNS))
    check("log records download times", log.count("Downloaded:") == len(fcd.COLUMNS))
    check("log records row counts", "Rows" in log and "Coverage:" in log)

    print("\nRaw files")
    raws = sorted(p.name for p in fcd.RAW_DIR.iterdir())
    check("raw files carry the download date", all(stamp in n for n in raws), str(raws))
    check("one raw file per source", len(raws) == 5, str(raws))

    failures = [n for n, ok, _ in CHECKS if not ok]
    print("\n" + "=" * 68)
    print(f"{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    if failures:
        print("FAILED: " + "; ".join(failures))
    print("=" * 68)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


def test_parsers():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())

# RPI clothing & footwear (CHBJ) trend drivers

Fetches five free monthly driver series for a rolling-origin backtest of RPI
clothing & footwear, and merges them into one tidy CSV.

## Usage

```bash
pip install -r requirements.txt
python fetch_clothing_drivers.py
```

Output (git-ignored, regenerate by re-running):

```
clothing_drivers/
  raw/                  untouched downloads, filename carries the download date
  drivers_monthly.csv   date, gbpusd, gbp_eri, china_import_px, gscpi, cotton_a, dmp_price_exp
  fetch_log.md          source URL, download time, first/last date, row count per series
```

`--only gbpusd,gscpi` restricts the run to a subset of columns.

Exit code is `0` when all six columns were fetched, `1` when any source failed,
so a monthly cron can alert on it.

## Series

| Column | Source | Series |
|---|---|---|
| `gbpusd` | Bank of England IADB | `XUMAUSS`, US$ into sterling, monthly average |
| `gbp_eri` | Bank of England IADB | `XUMABK67`, sterling effective exchange rate index |
| `china_import_px` | FRED | `CHNTOT`, US import price index by origin, China |
| `gscpi` | NY Fed | Global Supply Chain Pressure Index, monthly |
| `cotton_a` | World Bank Pink Sheet | Cotton A Index, US$/kg |
| `dmp_price_exp` | BoE Decision Maker Panel | Expected year-ahead own-price growth, % |

## Conventions

- History starts January 2000 where available; dates are `YYYY-MM-01`.
- All within-month date conventions (the BoE's end-of-month stamps, the Pink
  Sheet's `2026M08` labels) are normalised to the first of the month.
- Monthly averages. Missing values are left blank — nothing is forward-filled.
- Raw downloads are never rewritten; all parsing happens in code.
- A series that cannot be found or parsed is reported loudly, and its column is
  left blank. **A different series is never substituted for it.**

## Validation

Each run prints, and logs, per series: duplicate-date check, a staleness check
(last observation within 3 months of today), and a sanity range on the latest
value — `gbpusd` 1.1–1.5, `gbp_eri` 70–90, `gscpi` −2 to +5, `cotton_a` 1–4
$/kg, `dmp_price_exp` 0–10%. `china_import_px` is an index with no stable
expected level, so it is not range-checked.

## Tests

```bash
python tests/test_parsers.py
```

Builds synthetic files in each source's documented shape and runs the real
fetch functions against them with the network stubbed out. Covers date
normalisation, FRED's `.` missing-value marker, picking the right column
(Cotton not Brent; *expected* not *realised* DMP prices), the merge, blank
handling, and the validation warnings.

## Status: sources blocked in the authoring environment

The script was written and tested, but **no live data has been fetched**. Every
source host is denied by the egress policy of the sandbox it was authored in:

| Host | Result |
|---|---|
| `www.bankofengland.co.uk` | HTTP 403 on CONNECT (policy denial) |
| `fred.stlouisfed.org` | HTTP 403 on CONNECT |
| `www.newyorkfed.org` | HTTP 403 on CONNECT |
| `www.worldbank.org` | HTTP 403 on CONNECT |
| `thedocs.worldbank.org` | HTTP 403 on CONNECT |

Run it from a network that can reach those hosts to produce
`drivers_monthly.csv` and `fetch_log.md`.

Because the real files could never be opened, the three workbook parsers
(GSCPI, Pink Sheet, DMP) match on documented layout rather than a verified one,
and the DMP parser in particular selects its column by header text. They are
written to fail loudly rather than guess: on a mismatch the log names the
sheets and the candidate headers found, which is what you need to adjust them.
Check `fetch_log.md` on the first successful run — it records the exact sheet
and column used for each workbook series, and which DMP basis (single-month or
three-month average) was taken.

"""Forecast RPI fares and other travel (CHBR), month on month, with a track record.

WHY THIS EXISTS

The forecasts used to live in a conversation, and they moved: October went from
+0.1% to -1.5% to +0.9% to -1.9% inside one discussion, each time with a new
argument and none of the arguments tested before they were used. This module is
the fix. The method is fixed in code, every part of it is backtested on
2010-2025 with nothing allowed to see the year it forecasts, and changing it
means changing this file -- with the backtest in the diff.

THE METHOD, AND WHY EACH PART IS THERE

1. **Forecast air fares first, then translate.** The RPI fares group now moves
   0.54pp per 1pp of CPI air fares (R^2 0.97-0.99 within each year), up from
   0.44-0.47 in 2017-2023. Copying past RPI moves therefore under-forecasts any
   month whose comparison years are old: December by 3.9pp a year on average.
   So: median CPI air move (D7MB) over comparison years x this year's
   sensitivity + the median non-air part (rail, bus, other travel) over the
   last three years. That beat copying RPI moves in nine months of twelve,
   including every month from November to April; each month uses whichever
   route did better in its own backtest (`Backtest.route`).
2. **Comparison years are chosen for the calendar, not just recency.** The last
   three years, except:
   - December: the last three with the same Christmas alignment. ONS price a
     return trip (European +2 weeks, long-haul +3), so whether the index day
     puts the return after Christmas Day decides the month: since 2015, CPI air
     rose 46-61% in late-index-day Decembers and 16-40% in early ones.
   - January: the last three following a December of the same alignment.
   - March and April: the last five with the same Easter timing.
   2020 and 2021 are never used (fares largely imputed, travel restricted).
3. **Known policy changes are explicit adjustments** (`POLICY`), each with its
   reason and source, rather than folded silently into a number.
4. **Scraping adjusts the forecast, and is scored for doing so.** For each part
   of the basket the panel prices on *both* index days of a step, the scraped
   move is compared with that cell's seasonal norm from ONS's own sub-indices,
   and the weighted surprise is added at a reliability of 0.5 -- a stated prior,
   not an estimate -- until six months have been scored. After that the
   reliability is whatever the scores say it is.

THE INDEX DAY

ONS do not announce it in advance. Every date they have stated, and every date
implied by what they have said about return flights, is the Tuesday that falls
on the 9th-15th of the month (`index_day`). In December that is the 2nd Tuesday,
except when the 2nd Tuesday is the 8th: December 2020 used the 15th. December
2026 is that case again. `CONFIRMED_INDEX_DAYS` holds the stated dates, and a
test asserts the rule reproduces every one of them.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import math
import pathlib
import statistics as st
import sys
from typing import Any, Iterable, Mapping, Sequence

from .onscal import in_collection_window, target_departure_date
from .pull import choose_ordinal

log = logging.getLogger("ukairfares.nowcast")

Monthly = dict[tuple[int, int], float]
YM = tuple[int, int]

#: The mm23 series this module reads. Annual weights are not needed.
NEEDED = ("D7MB", "CZFN", "CHBR", "DOCW")

#: Fares were largely imputed and travel restricted; neither year is a guide.
COVID_YEARS = frozenset({2020, 2021})

#: Index days ONS have stated, or that follow exactly from what they stated.
CONFIRMED_INDEX_DAYS: dict[YM, dt.date] = {
    # December 2020 bulletin: "Index date for December: 15 December 2020".
    (2020, 12): dt.date(2020, 12, 15),
    # December 2022 bulletin: "Index date for December: 13 December 2022".
    (2022, 12): dt.date(2022, 12, 13),
    # ONS, December 2025 release: in December 2024 "the return date for the
    # European flights ... was Christmas Eve" -> departed 10 December.
    (2024, 12): dt.date(2024, 12, 10),
    # Same release: December 2025 European return was 23 December -> 9 December.
    (2025, 12): dt.date(2025, 12, 9),
    # July 2026 bulletin: "collected on or around 14 July 2026".
    (2026, 7): dt.date(2026, 7, 14),
}

FIRST_BACKTEST_YEAR = 2010
FIRST_DATA_YEAR = 2002     # air fares were collected quarterly before 2001
Z80 = 1.2816               # two-sided 80% interval

# --- Scraping ----------------------------------------------------------------
CELLS: tuple[tuple[str, int], ...] = (
    ("domestic", 1), ("european", 1), ("long_haul", 1),
    ("european", 3), ("long_haul", 3), ("long_haul", 6),
)
#: Share of the CPI air fares basket by advance window. Unverified: from a
#: regression on the published series, whose within-window split proved wrong.
WINDOW_SHARE = {1: 0.337, 3: 0.449, 6: 0.215}
#: Within-window shares. 1-month is recovered exactly from ONS's own
#: reconstruction rows; ONS publish no 3-month split, so the 1-month
#: European:long-haul ratio is assumed. 6-month is long-haul only.
WITHIN_WINDOW = {
    1: {"domestic": 0.098, "european": 0.710, "long_haul": 0.192},
    3: {"european": 0.787, "long_haul": 0.213},
    6: {"long_haul": 1.0},
}
#: Long-haul uses the cheapest comparable fare: its rule-selected fare moves
#: 10-28x more day to day than the price-blind cheapest (README, "the bank").
MEASURE = {"long_haul": "mean_cheapest_gbp"}
DEFAULT_MEASURE = "geomean_price_gbp"
#: Fewest collection days that may stand for one index day.
MIN_DAYS = 3
#: Weight on the scraped surprise until it has a record. A prior, not a fit.
PRIOR_RELIABILITY = 0.5
#: Scored months needed before the record replaces the prior.
MIN_SCORED = 6


@dataclasses.dataclass(frozen=True)
class Adjustment:
    year: int
    month: int
    rpi_pp: float
    sd_pp: float
    reason: str
    source: str


#: Known policy changes, applied on top of the statistical forecast.
POLICY: tuple[Adjustment, ...] = (
    Adjustment(
        2027, 1, -1.3, 0.65,
        "GBP2 single bus fare cap in England from 1 January 2027, down from GBP3: "
        "bus index about -7% x bus share of the group about 0.18. Precedents: "
        "January 2023 (GBP2 cap introduced) bus -19.3% against a normal ~-6%; "
        "January 2025 (cap raised to GBP3) bus +0.9%.",
        "DfT announcement of 22 July 2026, via secondary coverage (gov.uk not reached)",
    ),
    Adjustment(
        2027, 3, -0.2, 0.2,
        "Regulated rail fares frozen until March 2027 with nothing decided after "
        "it (as of October 2026). The non-air March baseline carries rises of "
        "~0.3-0.5pp from 2024-25; this is the midpoint of a freeze (-0.4) and an "
        "RPI-linked rise (0).",
        "National Rail fares freeze page; DfT",
    ),
)


# --- Calendar ----------------------------------------------------------------
def rule_index_day(year: int, month: int) -> dt.date:
    """The Tuesday falling on the 9th-15th: every date ONS have stated fits it."""
    ninth = dt.date(year, month, 9)
    return ninth + dt.timedelta(days=(1 - ninth.weekday()) % 7)


def index_day(year: int, month: int,
              overrides: Mapping[YM, dt.date] | None = None) -> dt.date:
    if overrides and (year, month) in overrides:
        return overrides[(year, month)]
    return CONFIRMED_INDEX_DAYS.get((year, month)) or rule_index_day(year, month)


def late_christmas(year: int, overrides: Mapping[YM, dt.date] | None = None) -> bool:
    """Does the European return (index day + 14) fall after Christmas Day?"""
    return index_day(year, 12, overrides) + dt.timedelta(days=14) > dt.date(year, 12, 25)


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    day = (h + l - 7 * m + 33 * month + 19) % 32
    return dt.date(year, month, day)


def easter_bucket(year: int) -> str:
    e = easter(year)
    if e.month == 3:
        return "early"
    return "late" if e.day <= 15 else "very_late"


def prev_month(year: int, month: int) -> YM:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def next_month(year: int, month: int) -> YM:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def select_analogues(month: int, year: int, available: Iterable[int],
                     overrides: Mapping[YM, dt.date] | None = None) -> list[int]:
    """Comparison years for `month` of `year`, most recent first."""
    prior = sorted((y for y in set(available) if y < year and y not in COVID_YEARS),
                   reverse=True)
    if month == 12:
        want = late_christmas(year, overrides)
        return [y for y in prior if late_christmas(y, overrides) == want][:3]
    if month == 1:
        want = late_christmas(year - 1, overrides)
        return [y for y in prior
                if (y - 1) not in COVID_YEARS and late_christmas(y - 1, overrides) == want][:3]
    if month in (3, 4):
        want = easter_bucket(year)
        return [y for y in prior if easter_bucket(y) == want][:5]
    return prior[:3]


# --- The statistical forecast -------------------------------------------------
def _slope(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 6:
        return None
    mx, my = st.mean(x), st.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx


def sensitivity(mm: Mapping[str, Monthly], year: int,
                months: Iterable[int] = range(1, 13)) -> float | None:
    """RPI fares move per 1pp of CPI air fares, fitted within one year."""
    pairs = [(mm["D7MB"][(year, m)], mm["CZFN"][(year, m)]) for m in months
             if (year, m) in mm["D7MB"] and (year, m) in mm["CZFN"]]
    return _slope([p[0] for p in pairs], [p[1] for p in pairs])


def nonair(mm: Mapping[str, Monthly], year: int, month: int) -> float | None:
    """What the group did beyond its air fares: rail, bus, taxis, ferries."""
    b = sensitivity(mm, year)
    if b is None or (year, month) not in mm["CZFN"] or (year, month) not in mm["D7MB"]:
        return None
    return mm["CZFN"][(year, month)] - b * mm["D7MB"][(year, month)]


def origin_year(year: int, month: int) -> int:
    """Whose January-September sensitivity is known when forecasting this month."""
    return year if month >= 10 else year - 1


@dataclasses.dataclass(frozen=True)
class Baseline:
    year: int
    month: int
    air: float
    nonair: float
    beta: float
    rpi: float
    copy_rpi: float | None
    analogues: tuple[int, ...]
    nonair_years: tuple[int, ...]


def baseline(mm: Mapping[str, Monthly], year: int, month: int,
             overrides: Mapping[YM, dt.date] | None = None) -> Baseline | None:
    beta = sensitivity(mm, origin_year(year, month), range(1, 10))
    if beta is None:
        return None
    air_years = [y for y in range(FIRST_DATA_YEAR, year) if (y, month) in mm["D7MB"]]
    S = select_analogues(month, year, air_years, overrides)
    na_years = [y for y in range(FIRST_DATA_YEAR, year)
                if y not in COVID_YEARS and nonair(mm, y, month) is not None]
    P = sorted(na_years, reverse=True)[:3]
    if not S or not P:
        return None
    air = st.median(mm["D7MB"][(y, month)] for y in S)
    na = st.median(nonair(mm, y, month) for y in P)
    copied = [mm["CZFN"][(y, month)] for y in S if (y, month) in mm["CZFN"]]
    return Baseline(year, month, air, na, beta, na + beta * air,
                    st.median(copied) if copied else None, tuple(S), tuple(P))


def _before(mm: Mapping[str, Monthly], cutoff: YM) -> dict[str, Monthly]:
    """Only what was published before `cutoff`: the backtest's guarantee."""
    return {c: {k: v for k, v in s.items() if k < cutoff} for c, s in mm.items()}


def _rmse(e: Sequence[tuple[int, float]]) -> float:
    return math.sqrt(st.mean(x * x for _, x in e)) if e else float("nan")


@dataclasses.dataclass(frozen=True)
class Backtest:
    """Both routes, scored the same way; the month uses whichever did better.

    The air route wins in nine months of twelve, and in every month from
    November to April, where the comparison years reach back to when the group
    moved less with air fares. Copying RPI moves wins narrowly in May, July and
    October, where the last three years are all recent anyway.
    """
    month: int
    air_errors: tuple[tuple[int, float], ...]     # (year, actual - forecast)
    copy_errors: tuple[tuple[int, float], ...]

    @property
    def air_rmse(self) -> float:
        return _rmse(self.air_errors)

    @property
    def copy_rmse(self) -> float:
        return _rmse(self.copy_errors)

    @property
    def route(self) -> str:
        return "copy" if self.copy_errors and self.copy_rmse < self.air_rmse else "air"

    @property
    def errors(self) -> tuple[tuple[int, float], ...]:
        return self.copy_errors if self.route == "copy" else self.air_errors

    @property
    def n(self) -> int:
        return len(self.errors)

    @property
    def rmse(self) -> float:
        return _rmse(self.errors)

    @property
    def bias(self) -> float:
        return st.mean(x for _, x in self.errors) if self.errors else float("nan")

    def within(self, pp: float) -> int:
        return sum(abs(x) <= pp for _, x in self.errors)


def backtest(mm: Mapping[str, Monthly], month: int, *, last_year: int | None = None,
             overrides: Mapping[YM, dt.date] | None = None) -> Backtest:
    last_year = last_year or max(y for y, _ in mm["CZFN"])
    errs, copy_errs = [], []
    for Y in range(FIRST_BACKTEST_YEAR, last_year + 1):
        if Y in COVID_YEARS or (Y, month) not in mm["CZFN"]:
            continue
        b = baseline(_before(mm, (Y, month)), Y, month, overrides)
        if b is None:
            continue
        actual = mm["CZFN"][(Y, month)]
        errs.append((Y, actual - b.rpi))
        if b.copy_rpi is not None:
            copy_errs.append((Y, actual - b.copy_rpi))
    return Backtest(month, tuple(errs), tuple(copy_errs))


def statistical(mm: Mapping[str, Monthly], year: int, month: int,
                overrides: Mapping[YM, dt.date] | None = None,
                cache: dict[int, Backtest] | None = None
                ) -> tuple[float, Baseline, Backtest] | None:
    """The statistical forecast for one month, by the route its backtest chose."""
    b = baseline(mm, year, month, overrides)
    if b is None:
        return None
    bt = cache.get(month) if cache is not None else None
    if bt is None:
        bt = backtest(mm, month, overrides=overrides)
        if cache is not None:
            cache[month] = bt
    value = b.copy_rpi if bt.route == "copy" and b.copy_rpi is not None else b.rpi
    return value, b, bt


# --- Scraping -----------------------------------------------------------------
def _date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def departure_of(row: Mapping[str, Any]) -> dt.date:
    """The departure a daily row priced: stated (schema 4+) or re-derived."""
    if row.get("departure_date"):
        return _date(row["departure_date"])
    scrape = _date(row["scrape_date"])
    return target_departure_date(scrape, int(row["months_ahead"]), choose_ordinal(scrape))


def panel_level(daily: Iterable[Mapping[str, Any]], haul: str, k: int,
                departure: dt.date) -> tuple[float, int] | None:
    """Geometric mean fare for one departure, over the collection window."""
    measure = MEASURE.get(haul, DEFAULT_MEASURE)
    cy, cm = departure.year, departure.month - k
    while cm < 1:
        cm += 12
        cy -= 1
    vals = []
    for r in daily:
        if r.get("haul_category") != haul or int(r.get("months_ahead", 0)) != k:
            continue
        scrape = _date(r["scrape_date"])
        if (scrape.year, scrape.month) != (cy, cm) or not in_collection_window(scrape):
            continue
        v = r.get(measure)
        if v and v > 0 and departure_of(r) == departure:
            vals.append(float(v))
    if len(vals) < MIN_DAYS:
        return None
    return math.exp(st.mean(math.log(v) for v in vals)), len(vals)


def cell_norm(sub: Mapping[tuple[str, int], Monthly], haul: str, k: int, year: int,
              month: int, overrides: Mapping[YM, dt.date] | None = None) -> float | None:
    """The cell's usual move into `month`, from ONS's own sub-index.

    None for January: the sub-indices are rebased to 100 every January, so a
    December-to-January step does not exist in them.
    """
    if month == 1:
        return None
    s = sub.get((haul, k), {})
    years = [y for y in range(FIRST_DATA_YEAR, year)
             if (y, month) in s and (y, month - 1) in s and s[(y, month - 1)] > 0]
    S = select_analogues(month, year, years, overrides)
    if not S:
        return None
    return st.median((s[(y, month)] / s[(y, month - 1)] - 1) * 100 for y in S)


@dataclasses.dataclass(frozen=True)
class CellStep:
    haul: str
    months_ahead: int
    weight: float
    scraped_pct: float
    norm_pct: float
    days: tuple[int, int]

    @property
    def surprise(self) -> float:
        return self.scraped_pct - self.norm_pct


@dataclasses.dataclass(frozen=True)
class ScrapeSignal:
    year: int
    month: int
    cells: tuple[CellStep, ...]

    @property
    def coverage(self) -> float:
        return sum(c.weight for c in self.cells)

    @property
    def air_surprise(self) -> float:
        """Weighted surprise in CPI air fares, pp, from the cells measured."""
        return sum(c.weight * c.surprise for c in self.cells)


def scrape_signal(daily: Sequence[Mapping[str, Any]],
                  sub: Mapping[tuple[str, int], Monthly], year: int, month: int,
                  overrides: Mapping[YM, dt.date] | None = None) -> ScrapeSignal:
    """Every cell the panel priced on both index days of the step into `month`."""
    py, pm = prev_month(year, month)
    d_prev, d_cur = index_day(py, pm, overrides), index_day(year, month, overrides)
    cells = []
    for haul, k in CELLS:
        norm = cell_norm(sub, haul, k, year, month, overrides)
        if norm is None:
            continue
        a = panel_level(daily, haul, k, d_prev)
        b = panel_level(daily, haul, k, d_cur)
        if a is None or b is None:
            continue
        cells.append(CellStep(haul, k, WINDOW_SHARE[k] * WITHIN_WINDOW[k][haul],
                              (b[0] / a[0] - 1) * 100, norm, (a[1], b[1])))
    return ScrapeSignal(year, month, tuple(cells))


@dataclasses.dataclass(frozen=True)
class Score:
    year: int
    month: int
    actual: float
    baseline: float
    scrape_rpi_pp: float      # beta x air surprise, before any reliability weight
    coverage: float

    def with_scraping(self, reliability: float) -> float:
        return self.baseline + reliability * self.scrape_rpi_pp


def reliability(scores: Sequence[Score]) -> tuple[float, str]:
    """How much of the scraped surprise to believe."""
    used = [s for s in scores if s.scrape_rpi_pp != 0]
    if len(used) < MIN_SCORED:
        return PRIOR_RELIABILITY, f"prior ({len(used)} of {MIN_SCORED} months scored)"
    sxx = sum(s.scrape_rpi_pp ** 2 for s in used)
    sxy = sum(s.scrape_rpi_pp * (s.actual - s.baseline) for s in used)
    fitted = min(1.0, max(0.0, sxy / sxx))
    return fitted, f"fitted on {len(used)} scored months"


# --- Assembly -----------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class MonthForecast:
    year: int
    month: int
    index_day: dt.date
    baseline: Baseline
    route: str
    statistical: float
    scrape: ScrapeSignal
    scrape_pp: float
    policy: tuple[Adjustment, ...]
    mom: float
    low: float
    high: float
    level: float | None
    yoy: float | None
    backtest: Backtest


@dataclasses.dataclass(frozen=True)
class Nowcast:
    last_published: YM
    last_level: float | None
    reliability: float
    reliability_basis: str
    months: tuple[MonthForecast, ...]
    scores: tuple[Score, ...]
    inputs: dict[str, Any]


def _panel_months(daily: Sequence[Mapping[str, Any]]) -> list[YM]:
    out = set()
    for r in daily:
        d = departure_of(r)
        out.add((d.year, d.month))
    return sorted(out)


def run(mm: Mapping[str, Monthly], sub: Mapping[tuple[str, int], Monthly],
        daily: Sequence[Mapping[str, Any]], *, horizon: int = 6,
        overrides: Mapping[YM, dt.date] | None = None,
        policy: Sequence[Adjustment] = POLICY,
        inputs: dict[str, Any] | None = None) -> Nowcast:
    last = max(mm["CZFN"])

    scores = []
    for ym in _panel_months(daily):
        if ym > last or ym not in mm["CZFN"]:
            continue
        # Everything here, including which route the backtest picks, sees only
        # what was published before the month being scored.
        stat = statistical(_before(mm, ym), *ym, overrides)
        sig = scrape_signal(daily, sub, *ym, overrides)
        if stat is None or not sig.cells:
            continue
        value, b, _ = stat
        scores.append(Score(*ym, mm["CZFN"][ym], value, b.beta * sig.air_surprise,
                            sig.coverage))
    rel, basis = reliability(scores)

    level = mm["CHBR"].get(last)
    if level is None:   # a hand-supplied MoM with no published level yet
        prior = max((k for k in mm["CHBR"] if k < last), default=None)
        if prior is not None:
            level = mm["CHBR"][prior]
            ym = prior
            while ym < last:
                ym = next_month(*ym)
                level *= 1 + mm["CZFN"][ym] / 100
    last_level = level

    months, backtests = [], {}
    ym = last
    for _ in range(horizon):
        ym = next_month(*ym)
        stat = statistical(mm, *ym, overrides, cache=backtests)
        if stat is None:
            break
        value, b, bt = stat
        sig = scrape_signal(daily, sub, *ym, overrides)
        scrape_pp = rel * b.beta * sig.air_surprise
        pol = tuple(a for a in policy if (a.year, a.month) == ym)
        mom = value + scrape_pp + sum(a.rpi_pp for a in pol)
        sd = math.sqrt(bt.rmse ** 2 + sum(a.sd_pp ** 2 for a in pol))
        yoy = None
        if level is not None:
            level *= 1 + mom / 100
            year_ago = mm["CHBR"].get((ym[0] - 1, ym[1]))
            yoy = (level / year_ago - 1) * 100 if year_ago else None
        months.append(MonthForecast(*ym, index_day(*ym, overrides), b, bt.route, value,
                                    sig, scrape_pp, pol,
                                    mom, mom - Z80 * sd, mom + Z80 * sd, level, yoy, bt))
    return Nowcast(last, last_level, rel, basis, tuple(months), tuple(scores), inputs or {})


# --- Inputs -------------------------------------------------------------------
def _ym(period: Any) -> YM:
    s = str(period)
    return int(s[:4]), int(s[5:7])


def mm23_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Monthly]:
    out: dict[str, Monthly] = {c: {} for c in NEEDED}
    for r in rows:
        c = r.get("cdid")
        if c in out and r.get("value") not in (None, ""):
            out[c][_ym(r["period"])] = float(r["value"])
    return out


def subindices_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, int], Monthly]:
    out: dict[tuple[str, int], Monthly] = {}
    for r in rows:
        if r.get("basis", "annual_january_100") != "annual_january_100":
            continue
        if r.get("index_value") is None:
            continue
        key = (r["haul_category"], int(r["months_ahead"]))
        out.setdefault(key, {})[_ym(r["index_month"])] = float(r["index_value"])
    return out


def parse_known(items: Iterable[str]) -> list[tuple[str, YM, float]]:
    """`CZFN:2026-10=1.6` -> ("CZFN", (2026, 10), 1.6)."""
    out = []
    for item in items:
        cdid, rest = item.split(":", 1)
        period, value = rest.split("=", 1)
        out.append((cdid.strip().upper(), _ym(period.strip()), float(value)))
    return out


def parse_index_days(items: Iterable[str]) -> dict[YM, dt.date]:
    """`2026-11=17` -> {(2026, 11): date(2026, 11, 17)}."""
    out = {}
    for item in items:
        period, day = item.split("=", 1)
        y, m = _ym(period.strip())
        out[(y, m)] = dt.date(y, m, int(day))
    return out


# --- Output -------------------------------------------------------------------
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _label(y: int, m: int) -> str:
    return f"{MONTHS[m - 1]} {y}"


def _pp(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v:+.{digits}f}"


def render(nc: Nowcast) -> str:
    L: list[str] = []
    ly, lm = nc.last_published
    L += ["# RPI fares and other travel: nowcast", "",
          f"Month-on-month change in CHBR. Last published month: **{_label(ly, lm)}**"
          + (f" (index {nc.last_level:.1f})." if nc.last_level else "."), ""]
    if nc.inputs:
        L += ["Inputs: " + "; ".join(f"{k} {v}" for k, v in nc.inputs.items()), ""]
    L += ["| Month | Index day | Statistical | Scraping (basket seen) | Policy | **Forecast** "
          "| 80% range | Backtest ±2pp / ±5pp | Level | y/y |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for f in nc.months:
        bt = f.backtest
        pol = _pp(sum(a.rpi_pp for a in f.policy)) if f.policy else "—"
        level = f"{f.level:.1f}" if f.level is not None else "—"
        yoy = f"{_pp(f.yoy)}%" if f.yoy is not None else "—"
        L.append(
            f"| {_label(f.year, f.month)} | {f.index_day:%d %b} | {_pp(f.statistical)} "
            f"| {_pp(f.scrape_pp)} ({f.scrape.coverage * 100:.0f}%) | {pol} "
            f"| **{_pp(f.mom)}%** | {_pp(f.low)} to {_pp(f.high)} "
            f"| {bt.within(2)}/{bt.n} · {bt.within(5)}/{bt.n} | {level} | {yoy} |")
    L += ["", "## How each month was built", ""]
    for f in nc.months:
        b = f.baseline
        years = ", ".join(map(str, b.analogues))
        if f.route == "copy":
            L.append(f"- **{_label(f.year, f.month)}** — median RPI move of {years} = "
                     f"{_pp(f.statistical)}% (copying RPI moves beat the air route in this "
                     f"month's backtest).")
        else:
            L.append(f"- **{_label(f.year, f.month)}** — air fares {_pp(b.air)}% (median of "
                     f"{years}) × sensitivity {b.beta:.3f} + non-air {_pp(b.nonair)}pp "
                     f"(median of {', '.join(map(str, b.nonair_years))}) = {_pp(b.rpi)}%.")
        for c in f.scrape.cells:
            L.append(f"  - scraped {c.haul} {c.months_ahead}m: {_pp(c.scraped_pct)}% against a "
                     f"norm of {_pp(c.norm_pct)}% (weight {c.weight:.3f}, days {c.days[0]}/{c.days[1]})")
        if not f.scrape.cells:
            L.append("  - scraping: no cell priced on both index days of this step")
        for a in f.policy:
            L.append(f"  - policy {_pp(a.rpi_pp)}pp: {a.reason} Source: {a.source}.")
    L += ["", "## Backtest, 2010 onwards (each year forecast from earlier years only)", "",
          "| Month | Route used | Years | Typical error | Bias | Within ±2pp | Within ±5pp "
          "| Air route | Copying RPI moves |",
          "|---|---|---|---|---|---|---|---|---|"]
    seen = set()
    for f in nc.months:
        bt = f.backtest
        if bt.month in seen:
            continue
        seen.add(bt.month)
        L.append(f"| {MONTHS[bt.month - 1]} | {bt.route} | {bt.n} | {bt.rmse:.2f}pp "
                 f"| {_pp(bt.bias, 2)}pp | {bt.within(2)}/{bt.n} | {bt.within(5)}/{bt.n} "
                 f"| {bt.air_rmse:.2f}pp | {bt.copy_rmse:.2f}pp |")
    L += ["", "## Scraping record", "",
          f"Reliability applied: **{nc.reliability:.2f}** — {nc.reliability_basis}.", ""]
    if nc.scores:
        L += ["| Month | Actual | Statistical | With scraping | Basket seen |", "|---|---|---|---|---|"]
        for s in nc.scores:
            L.append(f"| {_label(s.year, s.month)} | {_pp(s.actual)}% | {_pp(s.baseline)}% "
                     f"| {_pp(s.with_scraping(nc.reliability))}% | {s.coverage * 100:.0f}% |")
    else:
        L.append("No published month yet has scraped prices on both of its index days.")
    return "\n".join(L) + "\n"


def to_json(nc: Nowcast) -> dict[str, Any]:
    def conv(o: Any) -> Any:
        if dataclasses.is_dataclass(o):
            return {k: conv(v) for k, v in dataclasses.asdict(o).items()}
        if isinstance(o, dict):
            return {str(k): conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(v) for v in o]
        if isinstance(o, dt.date):
            return o.isoformat()
        if isinstance(o, float):
            return None if math.isnan(o) else round(o, 4)   # NaN is not JSON
        return o
    out = conv(nc)
    for m, f in zip(out["months"], nc.months):
        bt = f.backtest
        m["backtest_summary"] = conv({"route": bt.route, "rmse": bt.rmse, "bias": bt.bias,
                                      "n": bt.n, "within_2pp": bt.within(2),
                                      "within_5pp": bt.within(5), "air_rmse": bt.air_rmse,
                                      "copy_rpi_rmse": bt.copy_rmse})
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Nowcast RPI fares and other travel (CHBR).")
    p.add_argument("--export", type=pathlib.Path, default=pathlib.Path("reports/data/analytics.json"),
                   help="analytics export: panel, ONS sub-indices and (schema 3+) mm23 series")
    p.add_argument("--mm23", type=pathlib.Path, default=None,
                   help="mm23 rows (JSON list, or an export) when the export lacks them")
    p.add_argument("--known", action="append", default=[], metavar="CDID:YYYY-MM=VALUE",
                   help="a published value not yet in the data, e.g. CZFN:2026-10=1.6")
    p.add_argument("--index-day", action="append", default=[], metavar="YYYY-MM=DD",
                   help="override the index day for a month, e.g. 2026-11=17")
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--out-md", type=pathlib.Path, default=None)
    p.add_argument("--out-json", type=pathlib.Path, default=None)
    p.add_argument("--print", action="store_true", dest="echo")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

    export = json.loads(args.export.read_text(encoding="utf-8"))
    rows = export.get("mm23_series") or []
    if args.mm23:
        extra = json.loads(args.mm23.read_text(encoding="utf-8"))
        rows = extra.get("mm23_series", []) if isinstance(extra, dict) else extra
    mm = mm23_from_rows(rows)
    for cdid, ym, value in parse_known(args.known):
        mm.setdefault(cdid, {})[ym] = value
    missing = [c for c in ("D7MB", "CZFN", "CHBR") if not mm.get(c)]
    if missing:
        print(f"::error::no mm23 data for {', '.join(missing)}; run the ONS backfill "
              "or pass --mm23", file=sys.stderr)
        return 2

    sub = subindices_from_rows(export.get("published_series") or [])
    daily = export.get("daily_by_series") or []
    cov = export.get("coverage") or {}
    inputs = {
        "export generated": export.get("generated_ts", "?"),
        "panel to": cov.get("panel_last_day", "?"),
        "mm23 from": f"{args.mm23.name} (--mm23)" if args.mm23 else "the export",
        "mm23 to": "%d-%02d" % max(mm["D7MB"]),
        "hand-supplied": ", ".join(args.known) or "none",
        "index-day overrides": ", ".join(args.index_day) or "none",
    }
    nc = run(mm, sub, daily, horizon=args.horizon,
             overrides=parse_index_days(args.index_day), inputs=inputs)
    text = render(nc)
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(text, encoding="utf-8")
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(to_json(nc), indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
    if args.echo or not (args.out_md or args.out_json):
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""ONS index-day calendar arithmetic.

Everything in this pipeline hangs off one question: *which flights would an ONS
price collector have priced, on which day?*

Per ONS FOI-2023-1164 ("Aggregate index of air fares methodology") and the ONS
CPI/RPI Technical Manual s9.5.5:

  * Collection happens on "index day", usually the 2nd or 3rd Tuesday of the
    month. The exact day is withheld in advance (ONS consider it commercially
    sensitive) and confirmed retrospectively in the following month's CPI
    bulletin, under "methodology information".
  * Prices are collected "for flights which depart on 'index day' where
    possible". So the *departure* date is itself an index day, in a future
    month -- not a rolling `today + 30` offset.
  * Advance windows by haul: domestic 1 month; short-haul/European 1 and 3
    months; long-haul 1, 3 and 6 months.
  * Return flights are included in the price: +1 week domestic, +2 weeks
    short-haul, +3 weeks long-haul.
  * The airline chosen is the one "with a departure flight closest to a
    pre-specified time on a particular day" -- i.e. a fixed target time-of-day,
    held constant month to month. Not the cheapest fare on the day.

The practical consequence, and the reason this module exists rather than a
one-line `timedelta(days=30)`: a rolling day-offset lands on a different
weekday every month, and day-of-week is one of the largest single drivers of
air fare level. Anchoring both collection and departure to Tuesdays removes a
large spurious seasonal wobble that would otherwise swamp the signal we want.
"""

from __future__ import annotations

import calendar
import dataclasses
import datetime as dt
from typing import Iterator, Literal

TUESDAY = 1  # date.weekday(): Monday == 0

HaulCategory = Literal["domestic", "european", "long_haul"]

#: Advance-purchase windows, in whole months, by haul category (ONS FOI-2023-1164).
ADVANCE_MONTHS: dict[HaulCategory, tuple[int, ...]] = {
    "domestic": (1,),
    "european": (1, 3),
    "long_haul": (1, 3, 6),
}

#: Nominal `days_out` bucket labels. These are the conventional names for the
#: 1/3/6-month windows and are what the `days_out` column stores, so that
#: grouping is stable. The *actual* elapsed days vary (a 1-month window that
#: runs 2nd-Tuesday to 2nd-Tuesday is 28 days; 3rd-to-2nd is 35) and are stored
#: separately in `days_out_actual`.
NOMINAL_DAYS_OUT: dict[int, int] = {1: 30, 3: 90, 6: 180}

#: Return-leg duration by haul category (ONS FOI-2023-1164).
RETURN_LEG: dict[HaulCategory, dt.timedelta] = {
    "domestic": dt.timedelta(weeks=1),
    "european": dt.timedelta(weeks=2),
    "long_haul": dt.timedelta(weeks=3),
}

#: ONS pick the flight departing closest to a fixed target time, the same every
#: month. The real value is not published; this is our stand-in, chosen as a
#: mid-morning departure that essentially every route in the panel operates.
#: Held constant so that month-on-month movement is not contaminated by us
#: silently switching between a 06:00 and an 18:00 flight.
DEFAULT_TARGET_DEPARTURE_TIME = dt.time(9, 0)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """Return the `n`-th `weekday` of the given month (1-based).

    >>> nth_weekday(2026, 8, TUESDAY, 2)
    datetime.date(2026, 8, 11)
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (n - 1) * 7
    if day > calendar.monthrange(year, month)[1]:
        raise ValueError(f"no {n}th weekday {weekday} in {year}-{month:02d}")
    return dt.date(year, month, day)


def nth_tuesday(year: int, month: int, n: int) -> dt.date:
    return nth_weekday(year, month, TUESDAY, n)


def candidate_index_days(year: int, month: int) -> tuple[dt.date, dt.date]:
    """The two plausible index days for a month: 2nd and 3rd Tuesday.

    ONS say "usually the 2nd or 3rd Tuesday". Until the bulletin confirms which,
    both are live hypotheses and we collect across a window covering both.
    """
    return nth_tuesday(year, month, 2), nth_tuesday(year, month, 3)


def index_day_ordinal(day: dt.date) -> int | None:
    """Which Tuesday-of-the-month `day` is, or None if it is not a Tuesday.

    Used to read back the ordinal from a bulletin-confirmed index day, so that
    departure targeting can reuse the same ordinal in future months.
    """
    if day.weekday() != TUESDAY:
        return None
    return (day.day - 1) // 7 + 1


def add_months(d: dt.date, months: int) -> dt.date:
    """Shift a date by whole months, clamping the day to the target month's end."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def target_departure_date(
    collection_day: dt.date,
    months_ahead: int,
    ordinal: int | None = None,
) -> dt.date:
    """The departure date an ONS collector would price on `collection_day`.

    That is: the same-ordinal Tuesday of the month `months_ahead` months later.
    If `collection_day` is itself a Tuesday its ordinal is reused (so a 2nd-Tuesday
    collection targets 2nd-Tuesday departures); otherwise `ordinal` must be given.

    Falls back to the last Tuesday of the target month in the rare case that the
    requested ordinal does not exist there (e.g. a 5th Tuesday).
    """
    if ordinal is None:
        ordinal = index_day_ordinal(collection_day)
    if ordinal is None:
        raise ValueError(
            f"{collection_day} is not a Tuesday; pass an explicit `ordinal` "
            "to say which index day this collection is standing in for"
        )
    target_month = add_months(collection_day.replace(day=1), months_ahead)
    try:
        return nth_tuesday(target_month.year, target_month.month, ordinal)
    except ValueError:
        return nth_tuesday(target_month.year, target_month.month, ordinal - 1)


def return_date(departure: dt.date, haul: HaulCategory) -> dt.date:
    """Return-leg date. ONS price a return, not a one-way."""
    return departure + RETURN_LEG[haul]


@dataclasses.dataclass(frozen=True, slots=True)
class Leg:
    """One (route, advance-window) query an ONS collector would perform."""

    haul: HaulCategory
    months_ahead: int
    departure_date: dt.date
    return_date: dt.date
    #: Conventional 30/90/180 label for the window, for stable grouping.
    days_out: int
    #: Actual calendar days between collection and departure.
    days_out_actual: int
    #: Which Tuesday-of-month the departure lands on (our index-day hypothesis).
    index_day_ordinal: int


def legs_for(
    collection_day: dt.date,
    haul: HaulCategory,
    ordinal: int,
) -> Iterator[Leg]:
    """Every leg to price for one haul category on one collection day."""
    for months in ADVANCE_MONTHS[haul]:
        dep = target_departure_date(collection_day, months, ordinal=ordinal)
        yield Leg(
            haul=haul,
            months_ahead=months,
            departure_date=dep,
            return_date=return_date(dep, haul),
            days_out=NOMINAL_DAYS_OUT[months],
            days_out_actual=(dep - collection_day).days,
            index_day_ordinal=ordinal,
        )


def index_month_departure(departure: dt.date) -> dt.date:
    """Departure-month attribution: the fare belongs to the month it departs."""
    return departure.replace(day=1)


def index_month_collection(collection_day: dt.date) -> dt.date:
    """Collection-month attribution: the fare belongs to the month it was priced."""
    return collection_day.replace(day=1)


def in_collection_window(day: dt.date, start: int = 8, end: int = 21) -> bool:
    """Is `day` inside the daily-collection window?

    The window spans both candidate index days with margin either side, so that
    whichever Tuesday the bulletin later confirms, we already hold a scrape for
    it (and for the days around it, to measure fare drift).
    """
    return start <= day.day <= end

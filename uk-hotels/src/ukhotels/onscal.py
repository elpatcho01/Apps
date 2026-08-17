"""ONS accommodation collection calendar.

This is the sibling of `ukairfares.onscal`, and it is the module where the two
projects diverge most. Reading the air-fares version first will actively mislead
you, so the differences are set out here rather than left to be discovered.

WHAT ONS ACTUALLY DO (CPI item class 11.2.0.1, "Hotels, motels, inns and
similar accommodation services")
-----------------------------------------------------------------------------
The item has been rebuilt twice in nineteen months, and only the third design is
live:

  * **Before 2025.** One item: an overnight stay in a hotel on index day, in the
    middle of the month, priced *the day before the nominal stay* by internet
    and phone. Notoriously volatile -- collectors were pricing last-minute
    inventory, and sampled hotels were sometimes simply full, leaving nothing to
    price at all.
  * **2025.** A second item was added on the same collection method but priced
    *six weeks in advance*, and the existing item's weight was split across the
    two. The intent was more availability and less short-term demand pressure.
  * **2026.** The one-day-ahead item was **removed from the basket**, and the
    six-weeks-ahead item now prices **two separate nights each month** -- one in
    index week, and one the Thursday after index week, the second chosen to sit
    far enough from index day that any event-specific spike has passed.
    Collection for the second night began with February 2026 data.

So the live 2026 methodology is: **one advance window of six weeks, two
one-night weeknight stays per month.**

THE THREE THINGS THIS CHANGES RELATIVE TO AIR FARES
---------------------------------------------------
1. **Index day anchors the stay, not the collection.** For air fares, index day
   is both when the collector works and when the flight departs. Here the
   collector works six weeks *before* index week. Everything in this module is
   arithmetic in that direction, and the daily collection schedule sits roughly
   six weeks earlier in the calendar than the air-fares one.

2. **One window, not three.** There is no haul-category equivalent, and no
   1/3/6-month ladder. `ADVANCE_DAYS = 42`, full stop. The removed one-day-ahead
   window is retained as an opt-in diagnostic only (`LEGACY_ADVANCE_DAYS`),
   because it is what the pre-2026 published series was built on -- it is not
   part of the current methodology and is off by default.

3. **Both sampled nights are weeknights.** Tuesday (index week) and Thursday
   (the week after). There is no weekend leg to collect, which is a genuine
   surprise: hotel pricing is strongly weekday/weekend-segmented, and ONS
   deliberately sample only the weekday side of it.

WHAT IS GENUINELY AMBIGUOUS, AND SO IS STORED BOTH WAYS
-------------------------------------------------------
"Collected six weeks in advance ... for two separate nights ... at the same
time" does not pin down whether the two nights share one collection day or each
gets its own six-week lead. Both readings are defensible from the published
wording and they differ by nine days of price drift, so `CollectionAlignment`
computes each and every row records which produced it. Validation settles it.
Guessing and baking one in is exactly the mistake trap 10 warns about.
"""

from __future__ import annotations

import calendar
import dataclasses
import datetime as dt
from typing import Iterator, Literal

TUESDAY = 1  # date.weekday(): Monday == 0
THURSDAY = 3

#: The live advance-purchase window: six weeks between collection and stay.
ADVANCE_DAYS = 42

#: The pre-2026 one-day-ahead window. Removed from the basket for 2026, kept
#: here only so the pre-2026 published series can be reasoned about. Never
#: collected unless explicitly enabled -- it is not current methodology.
LEGACY_ADVANCE_DAYS = 1

#: Offset from index day to the second sampled night. Index day is a Tuesday;
#: "the Thursday after index week" is nine days later, not the Thursday inside
#: index week (which would be +2 and would defeat the stated purpose of putting
#: distance between the two observations).
THURSDAY_AFTER_INDEX_WEEK_OFFSET = 9

StayNightKind = Literal["index_week", "thursday_after"]
STAY_NIGHT_KINDS: tuple[StayNightKind, ...] = ("index_week", "thursday_after")

#: How the two nights map onto collection days. See the module docstring.
#:
#:   "per_night"  -- each night is priced exactly ADVANCE_DAYS before itself,
#:                   giving two collection days nine days apart.
#:   "single_day" -- both nights are priced on one day, ADVANCE_DAYS before the
#:                   index-week night, so the second night carries a 51-day lead.
CollectionAlignment = Literal["per_night", "single_day"]
COLLECTION_ALIGNMENTS: tuple[CollectionAlignment, ...] = ("per_night", "single_day")


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

    Identical to the air-fares rule -- index day is a property of the CPI
    collection round as a whole, not of any one item, and ONS confirm it
    retrospectively in the following month's bulletin either way.
    """
    return nth_tuesday(year, month, 2), nth_tuesday(year, month, 3)


def index_day_ordinal(day: dt.date) -> int | None:
    """Which Tuesday-of-the-month `day` is, or None if it is not a Tuesday."""
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


def stay_night(index_day: dt.date, kind: StayNightKind) -> dt.date:
    """The stay night of the given kind, for a month whose index day is `index_day`.

    `index_week` is index day itself -- the Tuesday night. `thursday_after` is
    nine days later.
    """
    if kind == "index_week":
        return index_day
    if kind == "thursday_after":
        night = index_day + dt.timedelta(days=THURSDAY_AFTER_INDEX_WEEK_OFFSET)
        # A sanity assertion rather than a computation: if index day is a
        # Tuesday, +9 is a Thursday. If it is not, the caller has handed us
        # something that is not an index day and every downstream date is wrong.
        if night.weekday() != THURSDAY:
            raise ValueError(
                f"{index_day} + 9 days is a {calendar.day_name[night.weekday()]}, "
                "not a Thursday -- index_day is not a Tuesday"
            )
        return night
    raise ValueError(f"unknown stay night kind {kind!r}")


def stay_nights(index_day: dt.date) -> tuple[tuple[StayNightKind, dt.date], ...]:
    """Both sampled nights for the month whose index day is `index_day`."""
    return tuple((kind, stay_night(index_day, kind)) for kind in STAY_NIGHT_KINDS)


def collection_date_for(
    night: dt.date,
    *,
    index_day: dt.date,
    alignment: CollectionAlignment = "per_night",
    advance_days: int = ADVANCE_DAYS,
) -> dt.date:
    """The day an ONS collector would price `night`, under one alignment reading.

    Under `per_night` the lead is exactly `advance_days` for both nights. Under
    `single_day` both nights are priced `advance_days` before the *index-week*
    night, so the Thursday carries a longer effective lead.
    """
    if alignment == "per_night":
        return night - dt.timedelta(days=advance_days)
    if alignment == "single_day":
        return index_day - dt.timedelta(days=advance_days)
    raise ValueError(f"unknown alignment {alignment!r}")


@dataclasses.dataclass(frozen=True, slots=True)
class Stay:
    """One (stay night, advance window) query an ONS collector would perform."""

    #: First of the CPI month this stay is priced for.
    index_month: dt.date
    #: The index day that anchors it (2nd or 3rd Tuesday of `index_month`).
    index_day: dt.date
    index_day_ordinal: int
    stay_night_kind: StayNightKind
    check_in: dt.date
    check_out: dt.date
    #: Nominal advance window in days. 42 for the live item.
    advance_days: int
    #: Actual days between collection and check-in. Equals `advance_days` under
    #: the per-night alignment and exceeds it for the Thursday under single-day.
    advance_days_actual: int
    alignment: CollectionAlignment

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


def stays_for_collection_day(
    collection_day: dt.date,
    *,
    index_day: dt.date,
    alignment: CollectionAlignment = "per_night",
    advance_days: int = ADVANCE_DAYS,
    tolerance_days: int = 0,
) -> Iterator[Stay]:
    """Every stay a collector working on `collection_day` would price.

    `index_day` is the (confirmed or hypothesised) index day of the CPI month
    being priced. `tolerance_days` widens the match so that a run which slips a
    day still prices the intended nights rather than silently collecting
    nothing -- the lead is then recorded as it actually was, in
    `advance_days_actual`, rather than as the nominal 42.
    """
    ordinal = index_day_ordinal(index_day)
    if ordinal is None:
        raise ValueError(f"{index_day} is not a Tuesday, so it is not an index day")

    for kind, night in stay_nights(index_day):
        due = collection_date_for(
            night, index_day=index_day, alignment=alignment, advance_days=advance_days
        )
        if abs((collection_day - due).days) > tolerance_days:
            continue
        yield Stay(
            index_month=index_day.replace(day=1),
            index_day=index_day,
            index_day_ordinal=ordinal,
            stay_night_kind=kind,
            check_in=night,
            # One night. ONS price an overnight stay, singular -- there is no
            # multi-night pattern to replicate.
            check_out=night + dt.timedelta(days=1),
            advance_days=advance_days,
            advance_days_actual=(night - collection_day).days,
            alignment=alignment,
        )


def collection_days_for_index_month(
    index_month: dt.date,
    *,
    advance_days: int = ADVANCE_DAYS,
) -> tuple[dt.date, ...]:
    """Every day on which some collection for `index_month` is due.

    Both index-day hypotheses (2nd and 3rd Tuesday), both nights, and both
    alignment readings -- because until the bulletin confirms the index day we
    do not know which of these is the real one, and a night we failed to price
    on the right day is unrecoverable. Four to six distinct dates in practice.
    """
    second, third = candidate_index_days(index_month.year, index_month.month)
    due: set[dt.date] = set()
    for index_day in (second, third):
        for _, night in stay_nights(index_day):
            for alignment in COLLECTION_ALIGNMENTS:
                due.add(
                    collection_date_for(
                        night,
                        index_day=index_day,
                        alignment=alignment,
                        advance_days=advance_days,
                    )
                )
    return tuple(sorted(due))


def index_months_in_scope(
    collection_day: dt.date, *, advance_days: int = ADVANCE_DAYS
) -> tuple[dt.date, ...]:
    """Which CPI months a collector working on `collection_day` could be pricing.

    A six-week lead straddles month boundaries, so a single collection day can
    legitimately serve one index month for one alignment and another for the
    other. Rather than reason about that arithmetic at every call site, the
    puller asks this and prices whatever it finds.
    """
    out: list[dt.date] = []
    for offset in (1, 2, 3):
        month = add_months(collection_day.replace(day=1), offset)
        if collection_day in collection_days_for_index_month(
            month, advance_days=advance_days
        ):
            out.append(month)
    return tuple(out)


def is_collection_day(
    day: dt.date, *, advance_days: int = ADVANCE_DAYS
) -> bool:
    """Is `day` a day on which some ONS-equivalent collection is due?"""
    return bool(index_months_in_scope(day, advance_days=advance_days))


def in_collection_window(day: dt.date) -> bool:
    """Loose gate for the daily workflow: is `day` anywhere near a due date?

    The exact due dates are computed by `collection_days_for_index_month`; this
    is the cheap shell-side check the workflow uses to decide whether to spin up
    a job at all. Deliberately generous -- a wasted run costs a few pence of
    quota, a missed night costs an observation that cannot be recollected.
    """
    for delta in (-1, 0, 1):
        if is_collection_day(day + dt.timedelta(days=delta)):
            return True
    return False


def index_month_stay(check_in: dt.date, index_day: dt.date) -> dt.date:
    """Stay-month attribution: the price belongs to the month of the stay.

    This is the attribution ONS's own construction implies -- the item is "an
    overnight stay in index week", and the index month is the month index week
    falls in.
    """
    return index_day.replace(day=1)


def index_month_collection(collection_day: dt.date) -> dt.date:
    """Collection-month attribution: the price belongs to the month it was priced.

    Six weeks earlier, so for accommodation these two rules disagree far more
    often than they do for air fares -- typically by one or two whole months
    rather than by an occasional boundary case. That makes getting it right
    correspondingly more important, which is why both are stored.
    """
    return collection_day.replace(day=1)

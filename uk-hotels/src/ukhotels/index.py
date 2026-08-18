"""Index construction: turning mean nightly rates into something comparable to ONS.

THE PROBLEM
-----------
Our reconstruction produces a mean nightly rate in pounds. ONS publish an index
number -- on a January 2025 = 100 basis in the ad hoc release, or 2015 = 100 in
the published time series. These are not comparable in level and never will be,
because we are not sampling the same properties, the same room types or the same
board bases. Any attempt to match levels would be measuring our sample
composition, not the accommodation market.

THE APPROACH
------------
Contribute only the *change*, and take the level from ONS:

    nowcast_level(m) = published_level(m-1) x price_relative(m-1 -> m)

A splice. Like-for-like in the only sense that matters -- both sides are a
month-on-month price relative for the same CPI item -- and directly usable,
because the output is a level on ONS's own basis.

MATCHED SAMPLES, AND WHY THEY MATTER MORE HERE THAN FOR AIR FARES
-------------------------------------------------------------------
The air-fares project matches on route, and routes are stable: LHR-JFK exists
every month, so an unmatched aggregate is merely risky. Accommodation has no
stable unit below the location. A hotel closes for refurbishment, rebrands,
leaves the aggregator, or is simply full on the night we price -- and ONS's own
2025 and 2026 methodology changes were made precisely because sampled hotels
being fully booked left nothing to price.

So a property dropping out is not an edge case here, it is the normal monthly
condition, and an unmatched average would manufacture large phantom movements
every single month. Relatives are computed only over properties priced in both
months, keyed on the provider's stable token rather than on the name -- a
rebrand changes the name and keeps the token, and matching on names would read
one rebrand as a property leaving and a different one arriving.

METHODOLOGY BREAKS
------------------
This item was rebuilt twice in nineteen months (see `accommodation_published_index`). A
relative spanning a break is not a price movement, it is a change of
measurement, and `consecutive_pairs` refuses to compute one. That is the same
discipline the air-fares project applies to its nine missing lockdown months,
for a different but equally disqualifying reason.

ELEMENTARY AGGREGATE FORMULAS
-----------------------------
Which formula ONS use for this item specifically is not established by any
public source we could reach. Jevons is used for most CPI items so it is the
most likely, but all three standard formulas are computed and tagged so
validation settles it rather than us guessing.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics
from typing import Iterable, Literal, Mapping, Sequence

Formula = Literal["jevons", "dutot", "carli"]
FORMULAS: tuple[Formula, ...] = ("jevons", "dutot", "carli")

#: The ad hoc release presents this item's sub-indices on a January 2025 = 100
#: basis, which is also when the six-weeks-ahead item began.
BASE_MONTH = 1
BASE_VALUE = 100.0

#: Collection regimes. A relative that spans two of these is comparing different
#: measurements and is refused.
MethodologyEra = Literal[
    "pre_2025_one_day_ahead", "2025_split_weight", "2026_six_weeks_two_nights"
]

#: First month of each regime, from the ONS basket articles: the six-weeks-ahead
#: item began with the 2025 basket, the one-day-ahead item was removed for 2026,
#: and the second sampled night began with February 2026 data.
ERA_BOUNDARIES: tuple[tuple[dt.date, MethodologyEra], ...] = (
    (dt.date(1900, 1, 1), "pre_2025_one_day_ahead"),
    (dt.date(2025, 1, 1), "2025_split_weight"),
    (dt.date(2026, 2, 1), "2026_six_weeks_two_nights"),
)


class IndexError_(ValueError):
    """Index could not be constructed from the given inputs."""


def methodology_era(month: dt.date) -> MethodologyEra:
    """Which collection regime `month` belongs to."""
    era: MethodologyEra = "pre_2025_one_day_ahead"
    for start, name in ERA_BOUNDARIES:
        if month >= start:
            era = name
    return era


def spans_methodology_break(earlier: dt.date, later: dt.date) -> bool:
    """Would a relative between these months compare two different measurements?"""
    return methodology_era(earlier) != methodology_era(later)


@dataclasses.dataclass(frozen=True, slots=True)
class PriceRelative:
    """A matched-sample price relative between two periods."""

    formula: Formula
    value: float
    #: Properties priced in both periods, and so actually used.
    n_matched: int
    #: Properties present in one period but not the other, and so excluded. On
    #: this panel that number is routinely non-trivial, and watching it is how
    #: you notice the sample eroding.
    n_unmatched: int

    @property
    def pct_change(self) -> float:
        return (self.value - 1.0) * 100.0

    @property
    def match_rate(self) -> float:
        total = self.n_matched + self.n_unmatched
        return self.n_matched / total if total else 0.0


def matched_pairs(
    base: Mapping[str, float], current: Mapping[str, float]
) -> tuple[list[tuple[float, float]], int]:
    """Pair up rates for properties present in both periods.

    Keys are property tokens. Non-positive rates are treated as absent: a zero
    or negative rate is a data error, and letting one into a geometric mean
    would take the log of zero.
    """
    keys_base = {k for k, v in base.items() if v and v > 0}
    keys_current = {k for k, v in current.items() if v and v > 0}
    matched_keys = sorted(keys_base & keys_current)
    unmatched = len(keys_base ^ keys_current)
    return [(base[k], current[k]) for k in matched_keys], unmatched


def price_relative(
    base: Mapping[str, float],
    current: Mapping[str, float],
    formula: Formula = "jevons",
    *,
    min_matched: int = 3,
) -> PriceRelative:
    """Matched-sample price relative from `base` to `current`.

    `min_matched` guards against a relative computed on one or two properties,
    which for hotel rates is essentially noise -- a single conference in town
    moves one property by 200% without saying anything about the market.
    """
    pairs, unmatched = matched_pairs(base, current)
    if len(pairs) < min_matched:
        raise IndexError_(
            f"only {len(pairs)} matched propert(ies), need {min_matched}. "
            "Too few priced in both periods to compute a defensible relative."
        )

    if formula == "jevons":
        value = math.exp(statistics.fmean(math.log(c / b) for b, c in pairs))
    elif formula == "dutot":
        value = statistics.fmean(c for _, c in pairs) / statistics.fmean(b for b, _ in pairs)
    elif formula == "carli":
        value = statistics.fmean(c / b for b, c in pairs)
    else:
        raise IndexError_(f"unknown formula {formula!r}")

    return PriceRelative(
        formula=formula, value=value, n_matched=len(pairs), n_unmatched=unmatched
    )


def splice_nowcast(published_level: float, relative: PriceRelative | float) -> float:
    """Project ONS's last published level forward by our estimated change.

    The headline output: a level on ONS's own basis, directly comparable to what
    they will publish, built without reproducing any of their history.
    """
    value = relative.value if isinstance(relative, PriceRelative) else relative
    if published_level <= 0:
        raise IndexError_(f"published level must be positive, got {published_level}")
    return published_level * value


@dataclasses.dataclass(frozen=True, slots=True)
class IndexPoint:
    month: dt.date
    level: float
    relative: PriceRelative | None


def consecutive_pairs(
    months: Sequence[dt.date],
) -> list[tuple[dt.date, dt.date]]:
    """Adjacent month pairs that are safe to compute a relative across.

    Excludes pairs that are not one calendar month apart (a gap must not be
    treated as a one-month change, which would attribute several months of drift
    to one) and pairs spanning a methodology break.
    """
    ordered = sorted(months)
    out: list[tuple[dt.date, dt.date]] = []
    for prev, month in zip(ordered, ordered[1:]):
        apart = (month.year - prev.year) * 12 + (month.month - prev.month)
        if apart != 1:
            continue
        if spans_methodology_break(prev, month):
            continue
        out.append((prev, month))
    return out


def build_chained_index(
    monthly_rates: Sequence[tuple[dt.date, Mapping[str, float]]],
    formula: Formula = "jevons",
    *,
    min_matched: int = 3,
    base_value: float = BASE_VALUE,
) -> list[IndexPoint]:
    """Chain successive matched relatives into a continuous own-basis index.

    Each step matches only against the immediately preceding month, so the
    sample may drift over time without any single comparison being unmatched --
    the standard chaining trade-off, and the only workable one on a panel where
    properties come and go.

    This is our *own* series, useful for inspecting the panel's behaviour. It is
    not the thing to compare with ONS levels; use `splice_nowcast` for that.
    """
    if not monthly_rates:
        return []
    ordered = sorted(monthly_rates, key=lambda pair: pair[0])
    by_month = dict(ordered)
    points = [IndexPoint(month=ordered[0][0], level=base_value, relative=None)]

    # Keyed by the *later* month, since that is what the loop below has in hand.
    safe = {later: earlier for earlier, later in consecutive_pairs([m for m, _ in ordered])}
    for month in [m for m, _ in ordered[1:]]:
        prev = safe.get(month)
        if prev is None:
            # Break rather than fabricate. An unchainable gap -- or a
            # methodology break -- must not be papered over with a
            # carried-forward level that looks like real data.
            break
        try:
            rel = price_relative(
                by_month[prev], by_month[month], formula, min_matched=min_matched
            )
        except IndexError_:
            break
        points.append(
            IndexPoint(month=month, level=points[-1].level * rel.value, relative=rel)
        )
    return points


def detect_basis(series: Sequence[tuple[dt.date, float]], tolerance: float = 0.5) -> str:
    """Infer whether a published series resets each January or is chain-linked.

    Read off the data rather than assumed. The ad hoc release describes a
    "January 2025 = 100 basis", which could mean a single base month or an
    annual reset; if every January is 100 it resets, otherwise it does not.
    """
    januaries = [v for m, v in series if m.month == BASE_MONTH]
    if len(januaries) < 2:
        return "unknown"
    if all(abs(v - BASE_VALUE) <= tolerance for v in januaries):
        return "annual_january_100"
    return "single_base"


def to_month_on_month(series: Iterable[tuple[dt.date, float]]) -> list[tuple[dt.date, float]]:
    """Month-on-month percentage change of a level series.

    Consecutive, break-free pairs only -- see `consecutive_pairs`.
    """
    ordered = sorted(series, key=lambda pair: pair[0])
    by_month = dict(ordered)
    out: list[tuple[dt.date, float]] = []
    for prev, month in consecutive_pairs([m for m, _ in ordered]):
        if by_month[prev] <= 0:
            continue
        out.append((month, (by_month[month] - by_month[prev]) / by_month[prev] * 100.0))
    return out

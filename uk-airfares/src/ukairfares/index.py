"""Index construction: turning mean fares into something comparable to ONS.

THE PROBLEM
-----------
Our reconstruction produces a mean fare in pounds. ONS publish an index number
on a January = 100 basis. These are not comparable in level and never will be,
because we are not sampling the same routes, carriers or fare classes -- ONS's
sample is not public. Any attempt to match levels is measuring our sample
composition, not the fare market.

THE APPROACH
------------
Contribute only the *change*, and take the level from ONS:

    nowcast_level(m) = published_level(m-1) x price_relative(m-1 -> m)

This is a splice. It is like-for-like in the only sense that matters -- both
sides are a month-on-month price relative for the same item -- and it is
directly usable: the output is a level on ONS's own basis, comparable to what
they will publish, without ever reproducing their history.

MATCHED SAMPLES
---------------
The price relative is computed over routes priced in *both* months, never over
whatever happened to return data each month. This is not fussiness. If LHR-CPT
returns a £900 fare in March and nothing in April, an unmatched aggregate reads
the resulting drop in the average as a fall in prices, when nothing about the
fare market changed. With a demand-driven cache producing routine `no_data`
gaps, unmatched aggregation would generate large phantom movements every month.
Matching is also what CPI does: price relatives are computed on matched models.

ELEMENTARY AGGREGATE FORMULAS
-----------------------------
Which formula ONS use for item 07.3.3 specifically is not something we could
establish from public sources. ONS use Jevons for most CPI items, so it is the
most likely, but all three standard formulas are computed and tagged so that
validation settles it against published values rather than us guessing:

  * Jevons -- geometric mean of price relatives. Standard for most CPI items.
  * Dutot  -- ratio of arithmetic means. Sensitive to expensive items.
  * Carli  -- arithmetic mean of price relatives. Known upward bias; included
              for completeness because it is what a naive implementation does.

On a matched sample, Jevons computed as GM(p_t)/GM(p_0) and as GM(p_t/p_0) are
algebraically identical; we use the relatives form so the matching is explicit.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics
from typing import Iterable, Literal, Mapping, Sequence

Formula = Literal["jevons", "dutot", "carli"]
FORMULAS: tuple[Formula, ...] = ("jevons", "dutot", "carli")

#: UK CPI chain-links annually, and the ONS ad hoc release presents these
#: sub-indices "on the January (of each year) = 100 basis".
BASE_MONTH = 1
BASE_VALUE = 100.0


class IndexError_(ValueError):
    """Index could not be constructed from the given inputs."""


@dataclasses.dataclass(frozen=True, slots=True)
class PriceRelative:
    """A matched-sample price relative between two periods."""

    formula: Formula
    value: float
    #: Routes priced in both periods, and so actually used.
    n_matched: int
    #: Routes present in one period but not the other, and so excluded.
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
    """Pair up prices for routes present in both periods.

    Returns the matched (base, current) pairs and the count of unmatched routes.
    Non-positive prices are treated as absent: a zero or negative fare is a data
    error, and letting one into a geometric mean would take the log of zero.
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

    `min_matched` guards against a relative computed on one or two routes, which
    for air fares is essentially noise -- an index built on it would swing wildly
    for reasons that have nothing to do with the market.
    """
    pairs, unmatched = matched_pairs(base, current)
    if len(pairs) < min_matched:
        raise IndexError_(
            f"only {len(pairs)} matched route(s), need {min_matched}. "
            "Too few routes priced in both periods to compute a defensible relative."
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

    This is the headline output: a level on ONS's own basis, directly comparable
    to what they will publish, built without reproducing any of their history.
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


def build_chained_index(
    monthly_prices: Sequence[tuple[dt.date, Mapping[str, float]]],
    formula: Formula = "jevons",
    *,
    min_matched: int = 3,
    base_value: float = BASE_VALUE,
) -> list[IndexPoint]:
    """Chain successive matched relatives into a continuous own-basis index.

    Each step matches only against the immediately preceding month, so the
    sample may drift over time without any single comparison being unmatched --
    the standard chaining trade-off. The first month is the base.

    This is our *own* series, useful for inspecting our panel's behaviour. It is
    not the thing to compare with ONS levels; use `splice_nowcast` for that.
    """
    if not monthly_prices:
        return []
    ordered = sorted(monthly_prices, key=lambda pair: pair[0])
    points = [IndexPoint(month=ordered[0][0], level=base_value, relative=None)]

    for (_, prev_prices), (month, prices) in zip(ordered, ordered[1:]):
        try:
            rel = price_relative(prev_prices, prices, formula, min_matched=min_matched)
        except IndexError_:
            # Break rather than fabricate: an unchainable gap must not be papered
            # over with a carried-forward level that looks like real data.
            break
        points.append(
            IndexPoint(month=month, level=points[-1].level * rel.value, relative=rel)
        )
    return points


def rebase_to_january(points: Sequence[IndexPoint]) -> list[IndexPoint]:
    """Re-express a chained series on each year's January = 100.

    Mirrors how the ONS ad hoc release presents these sub-indices. Years with no
    January observation are dropped rather than based on an arbitrary month,
    since a series based on August = 100 is not comparable to one based on
    January = 100 and silently mixing them would be worse than omitting them.
    """
    januaries = {p.month.year: p.level for p in points if p.month.month == BASE_MONTH}
    out: list[IndexPoint] = []
    for point in points:
        base = januaries.get(point.month.year)
        if not base:
            continue
        out.append(
            IndexPoint(
                month=point.month,
                level=point.level / base * BASE_VALUE,
                relative=point.relative,
            )
        )
    return out


def detect_basis(series: Sequence[tuple[dt.date, float]], tolerance: float = 0.5) -> str:
    """Infer whether a published series resets each January or is chain-linked.

    The ONS ad hoc release describes a "January (of each year) = 100 basis", but
    whether the published sub-indices literally reset every January or are
    chained into a continuous series is not clear from the description alone.
    Rather than guess, this reads the answer off the backfilled data: if every
    January is 100, it resets; otherwise it is chained.

    Returns "annual_january_100", "chained", or "unknown".
    """
    januaries = [v for m, v in series if m.month == BASE_MONTH]
    if len(januaries) < 2:
        return "unknown"
    if all(abs(v - BASE_VALUE) <= tolerance for v in januaries):
        return "annual_january_100"
    return "chained"


def to_month_on_month(series: Iterable[tuple[dt.date, float]]) -> list[tuple[dt.date, float]]:
    """Month-on-month percentage change of a level series.

    Consecutive months only -- a gap in the series must not be silently treated
    as a one-month change, which would attribute several months of drift to one.
    """
    ordered = sorted(series, key=lambda pair: pair[0])
    out: list[tuple[dt.date, float]] = []
    for (prev_month, prev), (month, value) in zip(ordered, ordered[1:]):
        months_apart = (month.year - prev_month.year) * 12 + (month.month - prev_month.month)
        if months_apart != 1 or prev <= 0:
            continue
        out.append((month, (value - prev) / prev * 100.0))
    return out

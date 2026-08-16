"""Choosing which quote represents the route, the way ONS would.

The ONS Technical Manual is specific about this and it is easy to get wrong:

    "the airlines chosen are those with a departure flight closest to a
    pre-specified time on a particular day on randomly selected routes"

So the collector does **not** take the cheapest fare of the day. They fix a
target departure time, hold it constant month after month, and price whichever
flight departs nearest to it. That distinction matters: a cheapest-of-day rule
silently migrates between a 06:10 departure in one month and a 21:45 in the
next, and the resulting month-on-month "price change" is substantially just the
time-of-day fare curve moving under you.

We compute both rules on every observation. The ONS-rule price is the headline;
the cheapest-of-day price is retained alongside it so the gap between the two is
measurable rather than assumed.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

from .onscal import DEFAULT_TARGET_DEPARTURE_TIME
from .providers.base import FareQuote


@dataclasses.dataclass(frozen=True, slots=True)
class Selection:
    """The two candidate representative fares for one route/date query."""

    #: Flight departing closest to the fixed target time (the ONS rule).
    ons_rule: FareQuote | None
    #: Cheapest fare available for the same query, whatever time it departs.
    cheapest: FareQuote | None
    #: Minutes between the ONS-rule flight's departure and the target time.
    #: None when no quote carried a usable departure time.
    ons_rule_time_delta_minutes: int | None
    n_quotes: int

    @property
    def spread(self) -> Decimal | None:
        """How much dearer the ONS-rule flight is than the cheapest on the day."""
        if self.ons_rule is None or self.cheapest is None:
            return None
        return self.ons_rule.price - self.cheapest.price


def _minutes_from_target(quote: FareQuote, target: dt.time) -> int | None:
    if quote.departure_at is None:
        return None
    actual = quote.departure_at.time()
    a = actual.hour * 60 + actual.minute
    t = target.hour * 60 + target.minute
    # Wrap around midnight: 23:50 is 20 minutes from 00:10, not 1420.
    diff = abs(a - t)
    return min(diff, 24 * 60 - diff)


def select(
    quotes: tuple[FareQuote, ...] | list[FareQuote],
    *,
    target_time: dt.time = DEFAULT_TARGET_DEPARTURE_TIME,
) -> Selection:
    """Apply both selection rules to one query's quotes."""
    quotes = tuple(quotes)
    if not quotes:
        return Selection(
            ons_rule=None, cheapest=None, ons_rule_time_delta_minutes=None, n_quotes=0
        )

    cheapest = min(quotes, key=lambda q: q.price)

    timed = [(q, _minutes_from_target(q, target_time)) for q in quotes]
    timed = [(q, d) for q, d in timed if d is not None]

    if timed:
        # Tie-break on price so the choice is deterministic when two flights sit
        # equidistant from the target time.
        ons_quote, delta = min(timed, key=lambda pair: (pair[1], pair[0].price))
    else:
        # No usable departure times. Fall back to cheapest rather than dropping
        # the observation, and flag it by leaving the delta None so downstream
        # can tell this row did not really apply the ONS rule.
        ons_quote, delta = cheapest, None

    return Selection(
        ons_rule=ons_quote,
        cheapest=cheapest,
        ons_rule_time_delta_minutes=delta,
        n_quotes=len(quotes),
    )

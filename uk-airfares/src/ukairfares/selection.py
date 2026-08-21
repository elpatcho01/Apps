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
from typing import Sequence

from .onscal import DEFAULT_TARGET_DEPARTURE_TIME
from .providers.base import FareQuote

#: Reject any itinerary priced above this multiple of the cheapest comparable
#: one. A second line of defence behind the direct-flight filter, for absurd
#: fares that survive it. Loose enough not to distort genuine saver-vs-flexible
#: spreads (observed live: 1.1x on long-haul, 2.3x on a legacy domestic
#: carrier), tight enough to catch the 67x seen on day one.
DEFAULT_MAX_PRICE_RATIO = 5.0


def plausible_candidates(
    quotes: Sequence[FareQuote],
    *,
    max_price_ratio: float = DEFAULT_MAX_PRICE_RATIO,
) -> tuple[tuple[FareQuote, ...], str]:
    """Narrow a provider's result set to comparable products.

    WHY THIS EXISTS -- from the first day of live collection:

        LGW-EDI  £4,841  SWISS       09:25   (cheapest £72)
        LHR-ABZ  £3,215  Air France  08:55   (cheapest £135)

    SWISS does not fly Gatwick to Edinburgh and Air France does not fly Heathrow
    to Aberdeen. Those are *connecting* itineraries -- LGW-Zurich-EDI,
    LHR-Paris-ABZ -- that Google Flights surfaces alongside direct services.
    Nobody buys them, and an ONS collector pricing London-Edinburgh would price
    the direct BA or easyJet service.

    The ONS target-time rule is price-blind by design: it takes whichever flight
    departs nearest the target, whatever it costs. That is only safe if the
    candidates are comparable products. Over a raw metasearch result set they
    are not, and a £4,841 "domestic fare" enters the index.

    Two filters, in order:

    1. **Direct services only**, when any exist. A one-stop itinerary is a
       different product from a non-stop, not a cheaper or dearer version of it.
       Where a route genuinely has no direct service the whole set is kept, and
       the reason is reported.
    2. **Extreme-outlier cap** relative to the cheapest survivor, for absurd
       fares that are nominally direct.

    Returns the surviving candidates and a short label describing what was
    applied, so the filtering is auditable per row rather than invisible.
    """
    quotes = tuple(quotes)
    if not quotes:
        return (), "empty"

    direct = tuple(q for q in quotes if q.transfers == 0)
    if direct:
        pool, basis = direct, "direct_only"
    else:
        # Recorded rather than silently tolerated: it means either a genuinely
        # connection-only route or a provider not reporting segment counts.
        pool, basis = quotes, "no_direct_available"

    floor = min(q.price for q in pool)
    capped = tuple(q for q in pool if float(q.price) <= float(floor) * max_price_ratio)
    if len(capped) < len(pool):
        basis += "+outlier_capped"
    return (capped or pool), basis


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
    #: Candidates left after filtering to comparable products.
    n_considered: int = 0
    #: What the filter did -- "direct_only", "no_direct_available",
    #: optionally suffixed "+outlier_capped".
    candidate_basis: str = "empty"
    #: How many MORE minutes from the target the runner-up sat than the winner.
    #:
    #: This is a fragility gauge, and it exists because the failure it measures
    #: was invisible per row. Long-haul selections were flipping between two
    #: candidates on consecutive days -- 173 to 273 minutes and back -- which
    #: showed up only as day-to-day noise in the aggregate. A SMALL margin means
    #: the choice is a near-tie and one flight entering or leaving the provider's
    #: result set will change it; a LARGE margin means the winner is unambiguous.
    #:
    #: None when there is no runner-up (a single timed candidate), which is its
    #: own kind of fragile and distinguishable from a confident wide margin.
    selection_margin_minutes: int | None = None

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
    max_price_ratio: float = DEFAULT_MAX_PRICE_RATIO,
) -> Selection:
    """Apply both selection rules to one query's quotes.

    Both rules operate on the *same* filtered candidate set, so the spread
    between them measures the selection rule rather than the filter.
    """
    all_quotes = tuple(quotes)
    if not all_quotes:
        return Selection(
            ons_rule=None, cheapest=None, ons_rule_time_delta_minutes=None,
            n_quotes=0, n_considered=0, candidate_basis="empty",
        )

    quotes, basis = plausible_candidates(all_quotes, max_price_ratio=max_price_ratio)

    cheapest = min(quotes, key=lambda q: q.price)

    timed = [(q, _minutes_from_target(q, target_time)) for q in quotes]
    timed = [(q, d) for q, d in timed if d is not None]

    margin: int | None = None
    if timed:
        # Tie-break on price so the choice is deterministic when two flights sit
        # equidistant from the target time.
        ranked = sorted(timed, key=lambda pair: (pair[1], pair[0].price))
        ons_quote, delta = ranked[0]
        if len(ranked) > 1:
            margin = ranked[1][1] - delta
    else:
        # No usable departure times. Fall back to cheapest rather than dropping
        # the observation, and flag it by leaving the delta None so downstream
        # can tell this row did not really apply the ONS rule.
        ons_quote, delta = cheapest, None

    return Selection(
        ons_rule=ons_quote,
        cheapest=cheapest,
        ons_rule_time_delta_minutes=delta,
        n_quotes=len(all_quotes),
        n_considered=len(quotes),
        candidate_basis=basis,
        selection_margin_minutes=margin,
    )

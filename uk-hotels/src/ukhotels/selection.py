"""Narrowing a location's returned properties to a comparable product set.

THE FAILURE THIS EXISTS TO PREVENT
-----------------------------------
On the sibling air-fares project, the ONS selection rule -- "the flight
departing closest to a fixed target time, whatever it costs" -- is price-blind
by design. Applied over an unfiltered metasearch result set it selected a
GBP 4,841 Gatwick-Edinburgh "domestic fare" that was really a connection via
Zurich, and the first day of live collection produced a domestic average 1,306%
above the cheapest comparable fare.

Accommodation is worse, for a structural reason. A flight search returns
itineraries on one route; they are at least all the same journey. A hotel search
returns *everything in the city*: hostels, serviced apartments, whole-home
vacation rentals, and five-star suites, each with a headline nightly rate, each
looking like a valid observation. And a single property returns different rates
for the same night depending on cancellation terms, board and room type, so even
after picking the right property the rate is not yet pinned down.

So filtering happens before any selection rule, and what was filtered is
recorded on every row rather than left implicit.

THE FOUR CONTROLS, AND WHICH ONE WE CAN ACTUALLY APPLY
-------------------------------------------------------
+---------------------+------------------------------------------------------+
| Cancellation policy | NOT APPLIED, and this is the most consequential gap   |
|                     | in the pipeline. Refundable versus non-refundable is  |
|                     | routinely a 30-40% gap on an identical room, the      |
|                     | largest single contamination risk here -- and the     |
|                     | source does not expose it. A raw-key census over 214  |
|                     | live properties found `free_cancellation` in the key  |
|                     | set of NONE of them; a nested `prices` array carried  |
|                     | by ~17% is the only route, giving a known value for   |
|                     | about 6%. Holding the basis constant rejected 100%    |
|                     | of every cell and produced no panel at all.           |
|                     | The control itself is intact and correct -- set       |
|                     | RATE_BASIS=free_cancellation and it applies -- so     |
|                     | this is a source limitation, and it is a validation   |
|                     | blocker rather than a footnote.                       |
+---------------------+------------------------------------------------------+
| Room and occupancy  | APPLIED, at the query. Two adults, no children, one   |
|                     | night, every call, every month. Constants in `panel`, |
|                     | not configuration, so they cannot drift mid-series.   |
+---------------------+------------------------------------------------------+
| Taxes and fees      | APPLIED, by storing both. `price_gbp` follows the     |
|                     | configured basis; `price_before_taxes_gbp` is kept    |
|                     | alongside, and `tax_basis` records which is which. A  |
|                     | series that silently mixed them would carry a bias    |
|                     | that could never be unpicked afterwards.              |
+---------------------+------------------------------------------------------+
| Board basis         | NOT APPLIED. No provider implemented here reports it. |
| and room type       | Recorded as `unknown` in `comparability_basis`,       |
|                     | counted in the digest, and stated at the top of the   |
|                     | README. This is a real hole, not a rounding error:    |
|                     | a room-only and a breakfast-inclusive rate for the    |
|                     | same room differ by a real amount, and we cannot      |
|                     | currently tell them apart.                            |
+---------------------+------------------------------------------------------+

Behind those sits the same extreme-outlier cap the air-fares project uses,
because a filter can only reject what it can see.
"""

from __future__ import annotations

import dataclasses
import statistics
from decimal import Decimal
from typing import Literal, Sequence

from .panel import STAR_TIERS, StarTier, tier_for_class
from .providers.base import PropertyQuote

#: Reject any property whose rate is more than this multiple away from the
#: MEDIAN comparable rate in the same location and tier, in either direction.
#:
#: This is a deliberate divergence from the air-fares project, which caps at 5x
#: the *cheapest* comparable fare. That anchor works there because a nonsense
#: fare is always nonsense on the high side -- a connecting itinerary priced at
#: 67x the direct one. Hotel search results fail in both directions: a hostel
#: dorm bed, a mislabelled room, or a plain data error puts an absurdly *low*
#: rate in the set, and anchoring on the minimum then lets that one bad rate
#: evict every legitimate property in the cell. A £1 listing alongside a £140
#: four-star would leave the £1 as the only survivor, which is precisely
#: backwards.
#:
#: The median is robust to a single bad value at either end, still catches the
#: absurd high outlier, and cannot be dragged by the thing it is meant to reject.
DEFAULT_MAX_PRICE_RATIO = 5.0

#: Which rate basis to hold constant. "free_cancellation" and "non_refundable"
#: each give a coherent series; "any" does not.
#:
#: "any" is the current default -- forced by the source, not chosen; see
#: `config.Config.from_env`. It was written as the uncontrolled comparison case
#: and is now the operating mode, which is exactly the sort of drift that gets
#: forgotten. Hence the blocker in `validate` and the standing entry in the
#: digest: the compromise has to keep announcing itself.
RateBasis = Literal["free_cancellation", "non_refundable", "any"]

#: Which price to treat as the headline. "advertised" is the rate as displayed,
#: which is what a price collector reads off the screen; "before_taxes" is the
#: net figure. Both are always stored; this only decides which fills `price_gbp`.
TaxBasis = Literal["advertised", "before_taxes"]

#: Property types that are the same product as a hotel room. Vacation rentals
#: are excluded: a whole flat sleeping six is not a comparable to a double room,
#: and ONS's item is explicitly hotels, motels, inns and similar.
COMPARABLE_PROPERTY_TYPES = frozenset({"hotel", "motel", "inn", "aparthotel", None})


@dataclasses.dataclass(frozen=True, slots=True)
class ComparableSet:
    """The surviving properties for one (location, tier) cell, and why."""

    tier: StarTier
    properties: tuple[PropertyQuote, ...]
    #: What the filter did, e.g.
    #: "tier=upscale+rate=free_cancellation+board=unknown+outlier_capped".
    #: Recorded per row so filtering is auditable rather than invisible.
    basis: str
    n_returned: int
    n_considered: int
    #: Dropped because their rate basis did not match the configured one. High
    #: counts here mean the provider is mostly serving the other basis and the
    #: cell is thinner than the property count suggests.
    n_dropped_rate_basis: int
    #: Outside every tier: unrated, below 3-star, or above 4.5-star.
    n_dropped_tier: int
    n_dropped_property_type: int
    n_dropped_outlier: int
    #: In a DIFFERENT tier, so counted in that cell rather than lost. Without
    #: this the per-cell counts do not reconcile against `n_returned` and a
    #: reader has to guess where the difference went -- which makes the whole
    #: breakdown untrustworthy exactly when it is being relied on.
    n_other_tier: int = 0

    def reconciles(self) -> bool:
        """Do the counts account for every property the provider returned?"""
        return self.n_returned == (
            self.n_considered
            + self.n_dropped_property_type
            + self.n_dropped_tier
            + self.n_dropped_rate_basis
            + self.n_dropped_outlier
            + self.n_other_tier
        )

    @property
    def cheapest(self) -> PropertyQuote | None:
        if not self.properties:
            return None
        return min(self.properties, key=lambda p: p.price)

    def price_spread_ratio(self) -> float | None:
        """Dearest over cheapest within the comparable set.

        The diagnostic that caught the air-fares selection bug. If this is large
        after filtering, the "comparable" set is not comparable and the filter
        needs another control, not the index another caveat.
        """
        if len(self.properties) < 2:
            return None
        prices = [float(p.price) for p in self.properties]
        low = min(prices)
        return max(prices) / low if low > 0 else None


def _matches_rate_basis(quote: PropertyQuote, basis: RateBasis) -> bool:
    if basis == "any":
        return True
    if quote.free_cancellation is None:
        # Unknown is not a match. Letting unknowns through would mean the series
        # is a silent blend of both bases, which is the exact failure this
        # control exists to prevent -- and a thinner honest sample beats a
        # fuller contaminated one.
        return False
    return quote.free_cancellation is (basis == "free_cancellation")


def comparable_sets(
    quotes: Sequence[PropertyQuote],
    *,
    rate_basis: RateBasis = "free_cancellation",
    max_price_ratio: float = DEFAULT_MAX_PRICE_RATIO,
    tiers: Sequence[StarTier] | None = None,
) -> dict[StarTier, ComparableSet]:
    """Split one location's returned properties into comparable sets by tier.

    Filters, in order, because each one changes what "cheapest" means for the
    next: property type, star tier, rate basis, then the outlier cap relative to
    the cheapest survivor *within the tier*. Capping before tiering would let a
    budget hostel set the floor for a four-star cell.
    """
    wanted = tuple(tiers or STAR_TIERS)
    n_returned = len(quotes)

    typed: list[PropertyQuote] = []
    n_dropped_property_type = 0
    for quote in quotes:
        ptype = (quote.property_type or "").strip().lower() or None
        if ptype in COMPARABLE_PROPERTY_TYPES:
            typed.append(quote)
        else:
            n_dropped_property_type += 1

    by_tier: dict[StarTier, list[PropertyQuote]] = {t: [] for t in wanted}
    n_dropped_tier = 0
    for quote in typed:
        tier = tier_for_class(quote.hotel_class)
        if tier in by_tier:
            by_tier[tier].append(quote)
        else:
            n_dropped_tier += 1

    out: dict[StarTier, ComparableSet] = {}
    for tier, pool in by_tier.items():
        n_other_tier = sum(len(p) for t, p in by_tier.items() if t != tier)
        on_basis = [q for q in pool if _matches_rate_basis(q, rate_basis)]
        n_dropped_rate_basis = len(pool) - len(on_basis)

        n_dropped_outlier = 0
        survivors = tuple(on_basis)
        if survivors:
            anchor = statistics.median(float(q.price) for q in survivors)
            lo, hi = anchor / max_price_ratio, anchor * max_price_ratio
            capped = tuple(q for q in survivors if lo <= float(q.price) <= hi)
            n_dropped_outlier = len(survivors) - len(capped)
            # The median always survives its own bounds, so `capped` cannot be
            # empty. The fallback is kept anyway: it costs nothing and means a
            # future change to the anchor cannot silently empty a cell.
            survivors = capped or survivors

        parts = [f"tier={tier}", f"rate={rate_basis}", "board=unknown", "room=unknown"]
        if n_dropped_property_type:
            parts.append("non_hotel_excluded")
        if n_dropped_outlier:
            parts.append("outlier_capped")

        out[tier] = ComparableSet(
            tier=tier,
            properties=tuple(sorted(survivors, key=lambda q: q.property_token)),
            basis="+".join(parts),
            n_returned=n_returned,
            n_considered=len(survivors),
            n_dropped_rate_basis=n_dropped_rate_basis,
            n_dropped_tier=n_dropped_tier,
            n_dropped_property_type=n_dropped_property_type,
            n_dropped_outlier=n_dropped_outlier,
            n_other_tier=n_other_tier,
        )
    return out


def headline_price(quote: PropertyQuote, tax_basis: TaxBasis) -> Decimal | None:
    """The price that fills `price_gbp`, under the configured tax basis."""
    if tax_basis == "advertised":
        return quote.price
    if tax_basis == "before_taxes":
        # Falls back to the advertised rate when the provider did not separate
        # them, rather than dropping the observation. The fallback is visible:
        # `price_before_taxes_gbp` is NULL on exactly those rows.
        return quote.price_before_taxes if quote.price_before_taxes is not None else quote.price
    raise ValueError(f"unknown tax basis {tax_basis!r}")


def pick_panel_candidates(
    comparable: ComparableSet, *, n: int = 3
) -> tuple[PropertyQuote, ...]:
    """Choose properties to pin for a cell, price-blind and deterministically.

    ONS sample properties without reference to price and then re-price the same
    ones month after month. The analogue has to be price-blind too, or the panel
    is a selection of whatever was cheap on the day it was drawn and every later
    month is measured against a biased base.

    So: sort by `property_token` and take the first `n`. The token is opaque and
    price-independent, which makes this an arbitrary but reproducible draw --
    exactly what is wanted. Sorting by rating would bias toward well-reviewed
    properties, and sorting by price would defeat the purpose entirely.
    """
    return comparable.properties[:n]

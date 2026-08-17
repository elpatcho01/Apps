"""The location panel, the property panel, and ONS weights.

TWO PANELS, BECAUSE PROPERTIES CHURN AND PLACES DO NOT
------------------------------------------------------
The air-fares project has one panel: a list of routes. LHR-JFK exists every
month, so a route is a stable identity and the sample takes care of itself.

Accommodation has no such luxury, and this is the single biggest structural
difference between the two projects. A hotel closes for refurbishment, rebrands
from an independent to a chain, leaves the aggregator, or reopens two price
tiers up. None of that is a price movement, and all of it looks exactly like one
to a naive average. So the sample is split in two:

  * **Locations** are stable and committed here in code. Twelve, one per ONS
    region -- ONS weight expenditure across twelve UK regions and the ad hoc
    release publishes this item's sub-indices regionally, so region is the
    dimension ONS actually sample on and the one we mirror.

  * **Properties** are discovered from the provider at runtime and pinned by
    the provider's stable `property_token`, never by name. Names change on
    rebrand; tokens do not. `data/property_panel.csv` records the pinned sample
    and is committed so it survives the ephemeral Actions checkout.

WHY BOTH A PINNED PANEL AND A CENSUS
-------------------------------------
ONS re-price a fixed sample of properties and substitute when one drops out.
Pinning mirrors that. But pinning alone means a property leaving takes its
history with it, and we would be reconstructing from a sample that quietly
shrinks.

So collection records **every comparable property** returned for a location, not
just the pinned ones, and marks the pinned ones with `is_panel_property`.
Reconciliation then computes two variants -- `pinned_panel` and
`matched_census` -- and tags which produced each reconstruction. The census
variant computes month-on-month relatives on properties present in *both*
months, which is what CPI does and what survives churn. Neither is assumed
correct; validation scores them side by side.

WEIGHTS
-------
Not hardcoded. Regional weights for this specific item are not something we
could establish from a public source, so `weights.csv` ships a clearly marked
placeholder and `load_weights()` refuses to hand placeholders to the validation
path. Every aggregate computed from them carries `weights_are_placeholder` so a
placeholder-based number cannot be mistaken for a real one.
"""

from __future__ import annotations

import csv
import dataclasses
import pathlib
from typing import Iterator, Literal

DATA_DIR = pathlib.Path(__file__).parent / "data"
WEIGHTS_PATH = DATA_DIR / "weights.csv"
PROPERTY_PANEL_PATH = DATA_DIR / "property_panel.csv"

#: Star-rating tiers. ONS's own property-class stratification is not public, so
#: these are a proxy chosen to bracket the mainstream business/leisure hotel
#: market either side of the median. Deliberately excludes 1-2 star (hostels,
#: budget rooms with no comparable product across regions) and 5 star (thin in
#: most regions, and dominated by a handful of properties whose pricing is not
#: representative of anything).
StarTier = Literal["midscale", "upscale"]
STAR_TIERS: dict[StarTier, tuple[float, float]] = {
    "midscale": (3.0, 3.5),
    "upscale": (4.0, 4.5),
}


def tier_for_class(hotel_class: float | None) -> StarTier | None:
    """Map a provider star rating onto a tier, or None if it is outside both.

    None is a first-class answer, not a failure: a property with no star rating
    or a 5-star property is simply not in our comparable set, and saying so is
    how it stays out of the index rather than silently widening it.
    """
    if hotel_class is None:
        return None
    for tier, (lo, hi) in STAR_TIERS.items():
        if lo <= float(hotel_class) <= hi:
            return tier
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class Location:
    """One collection location: an ONS region and the city standing in for it."""

    region: str
    city: str
    #: What the provider is asked for. Kept explicit rather than derived from
    #: `city` so a query string can be tuned without changing the region key
    #: that everything downstream joins on.
    query: str
    #: Why this city represents this region, so the sample stays auditable
    #: rather than folkloric.
    rationale: str

    @property
    def code(self) -> str:
        """Stable identifier, e.g. "north_west". Matches the `location` column."""
        return self.region.lower().replace(" ", "_").replace("&", "and")


#: One location per ONS region. ONS price this item regionally and publish it
#: regionally; matching that granularity is what makes the reconstruction
#: comparable to the ad hoc release rather than to a national aggregate we would
#: then have to weight ourselves.
LOCATIONS: tuple[Location, ...] = (
    Location("North East", "Newcastle upon Tyne", "Newcastle upon Tyne, UK",
             "Largest North East conurbation; strong weekday business demand"),
    Location("North West", "Manchester", "Manchester, UK",
             "Largest regional hotel market outside London; deep mid-market supply"),
    Location("Yorkshire and The Humber", "Leeds", "Leeds, UK",
             "Regional business centre with a broad three/four-star base"),
    Location("East Midlands", "Nottingham", "Nottingham, UK",
             "Central East Midlands market, mixed business and leisure"),
    Location("West Midlands", "Birmingham", "Birmingham, UK",
             "Second-largest UK city; conference demand makes it a useful volatility case"),
    Location("East of England", "Cambridge", "Cambridge, UK",
             "Constrained supply and high weekday rates; the tight-market case"),
    Location("London", "London", "London, UK",
             "Largest single market and the heaviest regional weight"),
    Location("South East", "Brighton", "Brighton, UK",
             "Coastal leisure market with pronounced seasonality"),
    Location("South West", "Bristol", "Bristol, UK",
             "Largest South West market, year-round business demand"),
    Location("Wales", "Cardiff", "Cardiff, UK",
             "Welsh capital; event-driven spikes make it a good test of the Thursday night"),
    Location("Scotland", "Edinburgh", "Edinburgh, UK",
             "Extreme August festival seasonality; the hardest month to price"),
    Location("Northern Ireland", "Belfast", "Belfast, UK",
             "Only sizeable Northern Ireland market; ONS report NI separately"),
)

#: Occupancy and stay specification, held constant across every query and every
#: month. Changing any of these mid-series would put a step change into the
#: index that no amount of later analysis could unpick, so they are constants
#: rather than configuration.
ADULTS = 2
CHILDREN = 0
NIGHTS = 1
CURRENCY = "GBP"


def locations_by_region(region: str) -> tuple[Location, ...]:
    return tuple(loc for loc in LOCATIONS if loc.region == region)


def iter_locations() -> Iterator[Location]:
    yield from LOCATIONS


def expected_queries_per_collection_day(nights_due: int = 1) -> int:
    """Provider calls one collection day costs.

    One call per (location x stay night): the provider returns every property in
    the location for those dates, so properties are free after the first call.
    That is why this panel can be twelve regions deep on a hobby-tier quota.
    """
    return len(LOCATIONS) * nights_due


# ---------------------------------------------------------------------------
# Pinned property panel
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PanelProperty:
    """One pinned property, mirroring an ONS sampled property."""

    location: str
    tier: str
    #: Provider-stable identifier. The join key for everything, because it
    #: survives a rebrand and a name does not.
    property_token: str
    property_name: str
    first_seen: str
    #: Why this property is in the panel: "discovered" for the initial
    #: deterministic draw, or "substitute_for:<token>" when it replaced one that
    #: left. Substitutions are never silent.
    selection_basis: str


def load_property_panel(
    path: pathlib.Path | None = None,
) -> dict[tuple[str, str], tuple[PanelProperty, ...]]:
    """Load the pinned panel, keyed by (location, tier).

    An absent or empty file is not an error. It means the panel has not been
    discovered yet, and collection runs in census-only mode until it has --
    which still produces a usable matched-sample index. Refusing to collect
    because no panel is pinned would lose nights that cannot be recollected.
    """
    path = path or PROPERTY_PANEL_PATH
    out: dict[tuple[str, str], list[PanelProperty]] = {}
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        for row in csv.DictReader(lines):
            if not (row.get("property_token") or "").strip():
                continue
            prop = PanelProperty(
                location=row["location"].strip(),
                tier=row["tier"].strip(),
                property_token=row["property_token"].strip(),
                property_name=(row.get("property_name") or "").strip(),
                first_seen=(row.get("first_seen") or "").strip(),
                selection_basis=(row.get("selection_basis") or "discovered").strip(),
            )
            out.setdefault((prop.location, prop.tier), []).append(prop)
    return {k: tuple(v) for k, v in out.items()}


def panel_tokens(
    panel: dict[tuple[str, str], tuple[PanelProperty, ...]]
) -> frozenset[str]:
    return frozenset(p.property_token for props in panel.values() for p in props)


def write_property_panel(
    properties: list[PanelProperty], path: pathlib.Path | None = None
) -> pathlib.Path:
    """Write the pinned panel back out, sorted so re-discovery is diff-stable."""
    path = path or PROPERTY_PANEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        properties, key=lambda p: (p.location, p.tier, p.property_name, p.property_token)
    )
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(
            "# Pinned property sample, discovered from the provider and stable "
            "thereafter.\n"
            "# Keyed on property_token, never on name -- a rebrand changes the name "
            "and not the token.\n"
            "# Regenerate with: python -m ukhotels.discover "
            "(writing is the default; --dry-run-panel to preview)\n"
        )
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "location",
                "tier",
                "property_token",
                "property_name",
                "first_seen",
                "selection_basis",
            ],
        )
        writer.writeheader()
        for prop in rows:
            writer.writerow(dataclasses.asdict(prop))
    return path


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Weights:
    """ONS regional weights for one year, keyed by region code.

    Regional expenditure weights for this item are not published at the
    granularity we would need, so in practice these are placeholders until
    `onsweights` finds something better. The flag is what matters: it rides on
    every aggregate computed from them.
    """

    year: int
    by_region: dict[str, float]
    is_placeholder: bool

    def get(self, region: str) -> float | None:
        return self.by_region.get(region)

    def normalised(self) -> dict[str, float]:
        total = sum(self.by_region.values())
        if total <= 0:
            raise ValueError(f"weights for {self.year} sum to {total}")
        return {k: v / total for k, v in self.by_region.items()}


def load_weights(
    year: int,
    *,
    allow_placeholder: bool = False,
    path: pathlib.Path | None = None,
) -> Weights:
    """Load regional weights for `year`, falling back to the latest earlier year.

    Falling back is correct rather than lazy: CPI weights are set annually and
    carried forward within the year, so the most recent year on file at or
    before `year` is the one ONS would have been using.
    """
    path = path or WEIGHTS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing. Run `python -m ukhotels.onsweights` to fetch it."
        )

    by_year: dict[int, dict[str, float]] = {}
    placeholder_years: set[int] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        for row in csv.DictReader(lines):
            if not (row.get("year") or "").strip():
                continue
            y = int(row["year"])
            by_year.setdefault(y, {})[row["region"].strip()] = float(row["weight"])
            if (row.get("is_placeholder", "") or "").strip().lower() in {"1", "true", "yes"}:
                placeholder_years.add(y)

    if not by_year:
        raise ValueError(f"no weight rows in {path}")

    chosen_year = min(by_year)
    for y in sorted(by_year):
        if y <= year:
            chosen_year = y
        else:
            break

    is_placeholder = chosen_year in placeholder_years
    if is_placeholder and not allow_placeholder:
        raise ValueError(
            f"weights for {year} are placeholders, not real ONS figures. Run "
            f"`python -m ukhotels.onsweights` to populate {path}, or pass "
            "allow_placeholder=True for a deliberate smoke test."
        )
    return Weights(
        year=chosen_year,
        by_region=dict(by_year[chosen_year]),
        is_placeholder=is_placeholder,
    )

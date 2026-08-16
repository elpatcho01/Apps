"""The route panel.

A deliberate proxy, not a reproduction. ONS's actual route sample is not public
(it is randomly selected and treated as commercially sensitive), so the goal here
is a panel that is *representative in the same dimensions* ONS sample across --
haul type, leisure vs business character, and London airport mix -- and whose
aggregate we can then calibrate against ONS's published sub-indices.

Weights
-------
Sub-index weights (domestic / European / long-haul) are published by ONS in an
ad hoc release, "Domestic, European and long-haul airfares consumer prices
sub-indices", which carries both the sub-indices and the weights for 2017
onwards. Those weights are NOT hardcoded here: shipping invented numbers that
look authoritative is worse than shipping none. `weights.csv` holds a clearly
marked placeholder, and `load_weights()` refuses to hand back placeholder values
to the validation path unless explicitly forced. Drop the real figures in from
the ONS release to activate them.
"""

from __future__ import annotations

import csv
import dataclasses
import pathlib
from typing import Iterator

from .onscal import HaulCategory

WEIGHTS_PATH = pathlib.Path(__file__).parent / "data" / "weights.csv"


@dataclasses.dataclass(frozen=True, slots=True)
class Route:
    origin: str
    destination: str
    haul: HaulCategory
    #: Free-text note on why this route is in the panel, so the sample's
    #: composition stays auditable rather than folkloric.
    rationale: str
    #: Relative weight *within* its haul bucket. Kept at 1.0 across the board
    #: until there is route-level evidence to do otherwise -- ONS's own
    #: within-category route weighting is not published.
    weight: float = 1.0

    @property
    def code(self) -> str:
        """Stable identifier, e.g. "LHR-JFK". Matches the `route` column."""
        return f"{self.origin}-{self.destination}"


# ---------------------------------------------------------------------------
# Domestic -- collected 1 month ahead, return +1 week.
# ---------------------------------------------------------------------------
DOMESTIC: tuple[Route, ...] = (
    Route("LHR", "EDI", "domestic", "London-Edinburgh: busiest UK domestic trunk route"),
    Route("LGW", "EDI", "domestic", "Edinburgh from a second London airport, captures LCC pricing"),
    Route("LHR", "GLA", "domestic", "London-Glasgow: second Scottish trunk route"),
    Route("LHR", "BFS", "domestic", "London-Belfast International: Northern Ireland, LCC-dominated"),
    Route("LGW", "BFS", "domestic", "Belfast from Gatwick, LCC pricing comparison"),
    Route("LHR", "ABZ", "domestic", "London-Aberdeen: business/energy-sector demand, less elastic"),
    Route("LGW", "JER", "domestic", "London-Jersey: Channel Islands leisure, strong seasonality"),
    Route("STN", "EDI", "domestic", "Stansted-Edinburgh: pure-LCC domestic benchmark"),
)

# ---------------------------------------------------------------------------
# European / short-haul -- collected 1 and 3 months ahead, return +2 weeks.
# ---------------------------------------------------------------------------
EUROPEAN: tuple[Route, ...] = (
    Route("LGW", "AGP", "european", "London-Malaga: high-volume Spanish beach leisure"),
    Route("LGW", "ALC", "european", "London-Alicante: Costa Blanca leisure, heavy LCC"),
    # The brief lists "London-Amalfi". Amalfi has no airport; Naples (NAP) is the
    # Amalfi Coast gateway and is what an equivalent leisure route would be.
    Route("LGW", "NAP", "european", "London-Naples: Amalfi Coast gateway (brief said 'Amalfi'; NAP is the airport)"),
    Route("LHR", "CDG", "european", "London-Paris: short business hop, legacy-carrier pricing"),
    Route("LHR", "AMS", "european", "London-Amsterdam: dense business route, high frequency"),
    Route("LHR", "DUB", "european", "London-Dublin: business/VFR mix, treated as European not domestic"),
    Route("STN", "DUB", "european", "Dublin on a pure-LCC pairing"),
    Route("LGW", "PMI", "european", "London-Palma: Balearics leisure, extreme seasonality"),
    Route("LGW", "FAO", "european", "London-Faro: Algarve leisure"),
)

# ---------------------------------------------------------------------------
# Long-haul -- collected 1, 3 and 6 months ahead, return +3 weeks.
# ---------------------------------------------------------------------------
LONG_HAUL: tuple[Route, ...] = (
    Route("LHR", "JFK", "long_haul", "London-New York: highest-value long-haul route in the world"),
    Route("LGW", "JFK", "long_haul", "New York from Gatwick, leisure-weighted transatlantic"),
    Route("LHR", "DXB", "long_haul", "London-Dubai: Gulf hub, business and connecting traffic"),
    Route("LGW", "MCO", "long_haul", "London-Orlando: family leisure, sharp school-holiday seasonality"),
    Route("LHR", "CPT", "long_haul", "London-Cape Town: counter-seasonal southern-hemisphere leisure"),
    Route("LHR", "SIN", "long_haul", "London-Singapore: ultra-long-haul Asia-Pacific"),
)

PANEL: tuple[Route, ...] = DOMESTIC + EUROPEAN + LONG_HAUL

#: Cabin and passenger specification. ONS price the advertised economy fare for
#: a single adult -- no loyalty, member or corporate pricing.
CABIN_CLASS = "economy"
ADULTS = 1


def routes_by_haul(haul: HaulCategory) -> tuple[Route, ...]:
    return tuple(r for r in PANEL if r.haul == haul)


def iter_panel() -> Iterator[Route]:
    yield from PANEL


def expected_queries_per_run() -> int:
    """How many provider calls one full collection run costs.

    Useful for sizing against an API's rate limit or quota before committing to
    a plan: domestic x1 window, European x2, long-haul x3.
    """
    from .onscal import ADVANCE_MONTHS

    return sum(len(ADVANCE_MONTHS[r.haul]) for r in PANEL)


@dataclasses.dataclass(frozen=True, slots=True)
class Weights:
    """ONS sub-index weights for one year."""

    year: int
    domestic: float
    european: float
    long_haul: float
    is_placeholder: bool

    def get(self, haul: HaulCategory) -> float:
        return {
            "domestic": self.domestic,
            "european": self.european,
            "long_haul": self.long_haul,
        }[haul]

    def normalised(self) -> dict[HaulCategory, float]:
        total = self.domestic + self.european + self.long_haul
        if total <= 0:
            raise ValueError(f"weights for {self.year} sum to {total}")
        return {
            "domestic": self.domestic / total,
            "european": self.european / total,
            "long_haul": self.long_haul / total,
        }


def load_weights(
    year: int,
    *,
    allow_placeholder: bool = False,
    path: pathlib.Path | None = None,
) -> Weights:
    """Load ONS sub-index weights for `year`.

    Falls back to the most recent earlier year on file, which is the correct
    behaviour for CPI weights (they are set annually and carried forward).

    Raises if the only thing on file is the placeholder and `allow_placeholder`
    is False -- so that a validation run cannot silently report an aggregate
    built on made-up weights.
    """
    path = path or WEIGHTS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing. Populate it from the ONS ad hoc release "
            "'Domestic, European and long-haul airfares consumer prices sub-indices'."
        )

    rows: list[Weights] = []
    with path.open(newline="", encoding="utf-8") as fh:
        # Strip the explanatory comment block before parsing: csv.DictReader
        # would otherwise take the first '#' line as the header row.
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        for row in csv.DictReader(lines):
            if not (row.get("year") or "").strip():
                continue
            rows.append(
                Weights(
                    year=int(row["year"]),
                    domestic=float(row["domestic"]),
                    european=float(row["european"]),
                    long_haul=float(row["long_haul"]),
                    is_placeholder=row.get("is_placeholder", "").strip().lower()
                    in {"1", "true", "yes"},
                )
            )
    if not rows:
        raise ValueError(f"no weight rows in {path}")

    rows.sort(key=lambda w: w.year)
    chosen = rows[0]
    for w in rows:
        if w.year <= year:
            chosen = w
        else:
            break

    if chosen.is_placeholder and not allow_placeholder:
        raise ValueError(
            f"weights for {year} are placeholders, not real ONS figures. "
            f"Populate {path} from the ONS ad hoc release, or pass "
            "allow_placeholder=True if you are deliberately running a smoke test."
        )
    return chosen

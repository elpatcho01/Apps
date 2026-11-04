"""What "current" means for the two vintaged tables, defined once.

WHY THIS EXISTS: THE STORED `is_current` FLAG IS MEANINGLESS
-------------------------------------------------------------
Both vintaged tables carry an `is_current` column documented as "TRUE for the
latest vintage". Backfill and reconcile both write it as a literal `True`, and
the tables are append-only, so nothing ever sets an older row to FALSE. Every
row is "current" forever.

Found on 2026-11-04 in the committed export: 783 of 786 published keys had three
"current" rows, one per monthly backfill, so the digest's published-value count
was triple the truth. Reconstructions would have gone the same way from their
first month -- reconcile retries daily from the 15th to the 25th, which means up
to eleven "current" vintages per variant per month.

The flag cannot be repaired in place without UPDATE, which the panel's rules
forbid, and it is not needed: `fetched_ts` and `computed_ts` already order the
vintages. So "current" is computed at read time, here, and the stored column is
ignored. Never filter on it.

`validate` had always ranked by timestamp itself and was correct; `digest` and
`export` trusted the flag and were not. Three readers, two definitions of
current. That is how one of them drifted, so now there is one definition.

ALSO HERE: THE VOCABULARY THE JOIN NEEDS
----------------------------------------
Reconcile names its national aggregate `location = "all"`, consistent with its
other pooled dimensions (tier "all", night "both"). ONS's national series is
stored as `location = "uk"`. Validation joined on location equality, so a
national reconstruction could never meet the national answer key -- and since
the regional key ends in 2025-07, the pipeline could never have reached a
verdict other than INSUFFICIENT_DATA. The translation lives here, with both
spellings as named constants that backfill and reconcile also use, so the two
sides cannot drift apart again.

And the class. The published table holds three COICOP classes per month for the
national series (11.2.0.1 hotels, 11.2.0.2 campsites, 11.2 the aggregate). A
join without a class filter scores each reconstruction against all three.
"""

from __future__ import annotations

#: The class this pipeline reconstructs. Everything is scored against this and
#: nothing else: 11.2.0.2 is campsites and hostels, and 11.2 is the aggregate.
TARGET_COICOP = "11.2.0.1"

#: Reconcile's name for the weighted national aggregate.
NATIONAL_RECONSTRUCTION_LOCATION = "all"

#: The location ONS's national time series is stored under.
NATIONAL_PUBLISHED_LOCATION = "uk"

#: Published series that must never be read as what they claim to be, with why.
#: The rows stay in the table -- it is append-only -- but no reader sees them.
EXCLUDED_PUBLISHED_SERIES: dict[str, str] = {
    "d7ex": (
        "Loaded as CPI 11.2 Accommodation services from August to November "
        "2026. It is CPI INDEX 11.1.2 : CANTEENS -- one letter out from d7cx, "
        "and ONS answered 200 with the wrong class rather than a 404."
    ),
}

#: The documented identity of one published value: a vintage is a newer
#: measurement of the same thing exactly when these agree.
PUBLISHED_KEY: tuple[str, ...] = ("index_month", "location", "series_source", "coicop_class")

#: The documented identity of one reconstructed value. Every methodology
#: variant is its own key: a rescore under a different reading is not a newer
#: vintage of the old reading, it is a different number.
RECONSTRUCTION_KEY: tuple[str, ...] = (
    "index_month", "location", "property_tier", "stay_night_kind",
    "attribution_rule", "collection_alignment", "sample_rule", "agg_method",
)


def _excluded_literal() -> str:
    # Inlined, not bound: these are constants in this file, never caller input.
    return ", ".join(f"'{s}'" for s in sorted(EXCLUDED_PUBLISHED_SERIES))


def latest_published(table: str, *, where: str | None = None) -> str:
    """A subquery yielding only the latest vintage of each published value.

    `where` filters BEFORE ranking, so a newer vintage that fails it does not
    hide an older one that passes. That is the behaviour validate always had.

    `IFNULL` matters: the ad hoc regional rows have no series_id, and
    `NULL NOT IN (...)` is NULL, which would silently drop every one of them.
    """
    conditions = [f"IFNULL(series_id, '') NOT IN ({_excluded_literal()})"]
    if where:
        conditions.append(f"({where})")
    return f"""(
  SELECT * EXCEPT (_vintage_rank)
  FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY {", ".join(PUBLISHED_KEY)}
      ORDER BY fetched_ts DESC, run_id DESC
    ) AS _vintage_rank
    FROM `{table}`
    WHERE {" AND ".join(conditions)}
  )
  WHERE _vintage_rank = 1
)"""


def latest_reconstruction(table: str, *, where: str | None = None) -> str:
    """A subquery yielding only the latest vintage of each reconstruction variant."""
    clause = f"WHERE {where}" if where else ""
    return f"""(
  SELECT * EXCEPT (_vintage_rank)
  FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY {", ".join(RECONSTRUCTION_KEY)}
      ORDER BY computed_ts DESC, run_id DESC
    ) AS _vintage_rank
    FROM `{table}`
    {clause}
  )
  WHERE _vintage_rank = 1
)"""


def published_location_for(reconstruction_location: str) -> str:
    """The published location a reconstruction's location should join to."""
    if reconstruction_location == NATIONAL_RECONSTRUCTION_LOCATION:
        return NATIONAL_PUBLISHED_LOCATION
    return reconstruction_location


def published_location_sql(column: str) -> str:
    """`published_location_for`, as SQL, for use inside a join condition."""
    return (
        f"(CASE WHEN {column} = '{NATIONAL_RECONSTRUCTION_LOCATION}' "
        f"THEN '{NATIONAL_PUBLISHED_LOCATION}' ELSE {column} END)"
    )

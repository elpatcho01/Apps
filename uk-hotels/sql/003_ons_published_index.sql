-- ONS's own published accommodation values: the answer key.
--
-- Kept in its own table rather than stuffed into reconstructed_index, because
-- it covers months we never reconstructed and has no attribution rule, sample
-- rule or aggregation method -- those are properties of *our* method, not of
-- ONS's published output.
--
-- Append-only and vintaged: ONS revise, and a revision must not silently
-- overwrite the value we scored against last month. `fetched_ts` gives the
-- revision history; `is_current` marks the latest.
--
-- THREE SOURCES FEED THIS TABLE, WHICH IS WHY `series_source` EXISTS
--
--   "adhoc_regional"  The ad hoc release "Hotel overnight stays booked in
--                     advance: consumer prices sub-indices" -- regional
--                     sub-indices for the six-weeks-ahead item, on a
--                     January 2025 = 100 basis. The closest match to what this
--                     pipeline reconstructs, and the primary target.
--   "timeseries"      The published CPI/CPIH item-class indices (L7IE for
--                     11.2.0.1 hotels/motels/inns, L7IG for 11.2.0.2 holiday
--                     centres and hostels, and the 11.2 aggregate). National
--                     only, 2015 = 100, but decades of history.
--   "price_quotes"    Item indices derived from the consumption segment
--                     indices and price quotes microdata.
--
-- They are on different bases and different geographies and must never be
-- compared with each other, only each against the matching reconstruction.
-- Mixing them is the most obvious way to produce a confidently wrong number
-- here, so the basis and the source travel on every row.
--
-- A NOTE ON THE METHODOLOGY BREAKS, WHICH ARE REAL AND RECENT
-- Pre-2025 this item was priced ONE DAY before the stay. In 2025 a second item
-- priced six weeks ahead was added and the weight split. In 2026 the one-day
-- item was removed entirely and the six-week item went to two nights a month.
-- So a value from 2024 and a value from 2026 are not measurements of the same
-- thing. `methodology_era` records which regime a value belongs to, and
-- validation refuses to span the breaks.

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.ons_published_index` (
  index_month     DATE      NOT NULL OPTIONS(description="Partition key. First of the month."),
  location        STRING    NOT NULL OPTIONS(description='ONS region code, or "uk" for a national series.'),
  series_id       STRING             OPTIONS(description='Series identifier where one exists, e.g. "l7ie" for CPI INDEX 11.2.0.1.'),
  series_name     STRING             OPTIONS(description="Human-readable series name as published."),
  series_source   STRING    NOT NULL OPTIONS(description='"adhoc_regional" | "timeseries" | "price_quotes". See the header -- these are on different bases and different geographies and must never be compared with one another.'),
  coicop_class    STRING             OPTIONS(description='"11.2.0.1" (hotels, motels, inns and similar), "11.2.0.2" (holiday centres, camping sites, youth hostels) or "11.2" (the accommodation services aggregate). Note 11.2.0.2 is a SEPARATE class, not a subdivision of 11.2.0.1.'),
  index_value     NUMERIC            OPTIONS(description="Published index level."),
  basis           STRING             OPTIONS(description='Index basis, e.g. "january_2025_100" for the ad hoc release or "2015_100" for the time series. Detected from the data where possible rather than assumed.'),
  methodology_era STRING             OPTIONS(description='"pre_2025_one_day_ahead" | "2025_split_weight" | "2026_six_weeks_two_nights". Which collection regime produced the value. The item was rebuilt twice in nineteen months, so a series spanning these is not a consistent measurement and validation must not treat it as one.'),
  release_url     STRING             OPTIONS(description="Where this vintage came from."),
  release_label   STRING             OPTIONS(description="Human-readable release identifier, e.g. the coverage period in its title."),
  fetched_ts      TIMESTAMP          OPTIONS(description="When we retrieved it. Orders revision vintages."),
  is_current      BOOL               OPTIONS(description="TRUE for the latest vintage of this (index_month, location, series_source, coicop_class)."),
  run_id          STRING             OPTIONS(description="Groups rows written by a single backfill run.")
)
PARTITION BY index_month
CLUSTER BY series_source, location
OPTIONS(
  description="ONS published accommodation price indices from three distinct sources. The validation target. Append-only; revisions arrive as new vintages."
);

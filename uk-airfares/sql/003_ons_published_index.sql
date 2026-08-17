-- ONS's own published air fare sub-indices, backfilled from the ad hoc release.
--
-- This is the validation target: the answer key. Kept in its own table rather
-- than stuffed into reconstructed_index, because it covers months we never
-- reconstructed (the release runs from January 2017) and has no attribution
-- rule, selection rule or aggregation method -- those are properties of *our*
-- method, not of ONS's published output.
--
-- Append-only like everything else, and vintaged: ONS do revise, and a revision
-- must not silently overwrite the value we scored against last month. Ordering
-- by fetched_ts gives the revision history; is_current marks the latest.

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.ons_published_index` (
  index_month     DATE      NOT NULL OPTIONS(description="Partition key. First of the month."),
  haul_category   STRING    NOT NULL OPTIONS(description='"domestic" | "european" | "long_haul".'),
  months_ahead    INT64              OPTIONS(description="Advance window the series is for: 1, 3 or 6 months. ONS publish each haul category broken out by window -- six series in total, not three -- so this is part of the key."),
  index_value     NUMERIC            OPTIONS(description="Published index level."),
  basis           STRING             OPTIONS(description='How the series is based: "annual_january_100" or "chained". Detected from the data rather than assumed -- see index.detect_basis.'),
  release_url     STRING             OPTIONS(description="Ad hoc release this vintage came from."),
  release_label   STRING             OPTIONS(description="Human-readable release identifier, e.g. the coverage period in its title."),
  fetched_ts      TIMESTAMP          OPTIONS(description="When we retrieved it. Orders revision vintages."),
  is_current      BOOL               OPTIONS(description="TRUE for the latest vintage of this (index_month, haul_category)."),
  run_id          STRING             OPTIONS(description="Groups rows written by a single backfill run.")
)
PARTITION BY index_month
CLUSTER BY haul_category, months_ahead
OPTIONS(
  description="ONS published domestic/European/long-haul air fare sub-indices, backfilled from the ONS ad hoc release. The validation target. Append-only; revisions arrive as new vintages."
);

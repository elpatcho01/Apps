-- Monthly reconstruction, written once ONS confirm the index day.
--
-- Also append-only. A given (index_month, haul_category) may legitimately gain
-- several rows over time: one written as soon as the index day is confirmed,
-- another when the ad hoc release later backfills published_ons_value, another
-- if a methodology variant is rescored. `computed_ts` orders the vintages and
-- `is_current` marks the latest; nothing is ever edited in place.

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.reconstructed_index` (
  index_month          DATE    NOT NULL OPTIONS(description="Partition key. First of the month this reconstruction is for."),
  haul_category        STRING  NOT NULL OPTIONS(description='"domestic" | "european" | "long_haul" | "all" for the weighted aggregate.'),
  confirmed_index_day  DATE    NOT NULL OPTIONS(description="Index day as confirmed in the following month's CPI bulletin methodology section."),
  reconstructed_value  NUMERIC          OPTIONS(description="Headline reconstructed level for the category."),
  n_observations       INT64            OPTIONS(description="Panel rows feeding this value."),
  published_ons_value  NUMERIC          OPTIONS(description="ONS's own published sub-index, backfilled from the ad hoc release once available. NULL until then."),
  computed_ts          TIMESTAMP        OPTIONS(description="When this vintage was computed."),

  -- ---------------------------------------------------------------------
  -- Extensions
  -- ---------------------------------------------------------------------
  attribution_rule     STRING           OPTIONS(description='"departure_month" or "collection_month". Which attribution hypothesis this row scores -- both are computed so validation can settle which matches ONS.'),
  selection_rule       STRING           OPTIONS(description='"ons_target_time" or "cheapest".'),
  agg_method           STRING           OPTIONS(description='"mean" | "median" | "geometric_mean". ONS use a Jevons (geometric mean) elementary aggregate for most items, so all three are computed.'),

  mean_fare_gbp        NUMERIC          OPTIONS(description="Arithmetic mean fare across matching rows."),
  median_fare_gbp      NUMERIC          OPTIONS(description="Median fare across matching rows."),
  geomean_fare_gbp     NUMERIC          OPTIONS(description="Geometric mean fare -- the Jevons analogue."),

  index_day_exact      BOOL             OPTIONS(description="TRUE if we hold a scrape on the confirmed index day itself; FALSE if the nearest available date was substituted."),
  scrape_date_used     DATE             OPTIONS(description="The scrape_date actually aggregated. Differs from confirmed_index_day when index_day_exact is FALSE."),
  index_day_offset_days INT64           OPTIONS(description="scrape_date_used - confirmed_index_day. Non-zero means fare drift has crept in; a key covariate when interpreting error."),

  n_routes             INT64            OPTIONS(description="Distinct routes contributing."),
  n_expected_routes    INT64            OPTIONS(description="Routes the panel should have produced. Coverage = n_routes / n_expected_routes."),
  weights_are_placeholder BOOL          OPTIONS(description="TRUE if the aggregate used placeholder rather than real ONS weights. Such rows must not be reported as validated."),
  source_is_cached     BOOL             OPTIONS(description="TRUE if the underlying fares came from a cache-backed provider rather than live index-day quotes."),

  pipeline_version     STRING           OPTIONS(description="Version of the reconstruction logic, so methodology changes are traceable across vintages."),
  is_current           BOOL             OPTIONS(description="TRUE for the latest vintage of this (index_month, haul_category, attribution_rule, selection_rule, agg_method)."),
  run_id               STRING           OPTIONS(description="Groups rows written by a single reconciliation run.")
)
PARTITION BY index_month
CLUSTER BY haul_category, attribution_rule
OPTIONS(
  description="Reconstructed ONS air fare sub-indices, one vintage per computation. Append-only."
);

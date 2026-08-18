-- Monthly reconstruction, written once ONS confirm the index day.
--
-- Append-only. A given (index_month, location) legitimately gains rows over
-- time: one when the index day is confirmed, another when a published value is
-- backfilled, another if a variant is rescored under a different methodology
-- reading. `computed_ts` orders the vintages and `is_current` marks the latest;
-- nothing is ever edited in place.
--
-- WHY SO MANY VARIANT COLUMNS
-- Five things about this item's methodology are genuinely unresolved from
-- public sources, and each one changes the answer:
--
--   attribution_rule     stay month vs collection month  (six weeks apart)
--   collection_alignment per-night vs single-day lead    (nine days apart)
--   sample_rule          pinned panel vs matched census  (churn handling)
--   agg_method           Jevons vs Dutot vs Carli        (formula)
--   selection_scope      per-tier vs pooled tiers        (stratification)
--
-- Every combination is computed and tagged rather than one being guessed at and
-- baked in. Validation scores them side by side and the data settles it. That
-- is more rows than a single-variant table, but a reconstruction that cannot
-- say which reading produced it is not evidence of anything.

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.accommodation_reconstructed_index` (
  index_month          DATE    NOT NULL OPTIONS(description="Partition key. First of the CPI month this reconstruction is for."),
  location             STRING  NOT NULL OPTIONS(description='ONS region code, or "all" for the weight-weighted aggregate.'),
  property_tier        STRING           OPTIONS(description='"midscale", "upscale", or "all" when tiers are pooled.'),
  stay_night_kind      STRING           OPTIONS(description='"index_week", "thursday_after", or "both" when the two nights are combined. ONS publish one item covering both, but they are separate measurements and are reconstructed separately as well.'),
  confirmed_index_day  DATE    NOT NULL OPTIONS(description="Index day as confirmed in the following month's CPI bulletin methodology section."),
  reconstructed_value  NUMERIC          OPTIONS(description="Headline reconstructed level -- a mean nightly rate in pounds, NOT an index number. See the README on splicing: levels are not comparable with ONS's, movements are."),
  n_observations       INT64            OPTIONS(description="Property-nights feeding this value."),
  n_properties         INT64            OPTIONS(description="Distinct properties contributing."),
  published_ons_value  NUMERIC          OPTIONS(description="ONS's own published value, backfilled from the ad hoc release or the time series. NULL until then."),
  computed_ts          TIMESTAMP        OPTIONS(description="When this vintage was computed."),

  -- ---------------------------------------------------------------------
  -- Which methodology reading produced this row
  -- ---------------------------------------------------------------------
  attribution_rule     STRING           OPTIONS(description='"stay_month" or "collection_month". With a six-week lead these disagree by one to two whole months, so this is the highest-stakes of the five open questions.'),
  collection_alignment STRING           OPTIONS(description='"per_night" or "single_day".'),
  sample_rule          STRING           OPTIONS(description='"pinned_panel" (ONS-style fixed property sample) or "matched_census" (every comparable property present in both months). Properties churn in a way routes do not, so these diverge and neither is assumed correct.'),
  agg_method           STRING           OPTIONS(description='"mean" | "median" | "geometric_mean". ONS use a Jevons (geometric mean) elementary aggregate for most CPI items, so the geometric mean is the most likely match, but all three are carried.'),

  mean_rate_gbp        NUMERIC          OPTIONS(description="Arithmetic mean nightly rate across matching rows."),
  median_rate_gbp      NUMERIC          OPTIONS(description="Median nightly rate."),
  geomean_rate_gbp     NUMERIC          OPTIONS(description="Geometric mean nightly rate -- the Jevons analogue."),

  -- ---------------------------------------------------------------------
  -- Provenance and quality, all of which can downgrade a validation verdict
  -- ---------------------------------------------------------------------
  index_day_exact      BOOL             OPTIONS(description="TRUE if we hold scrapes collected on the date the confirmed index day implies; FALSE if a nearby collection date was substituted."),
  scrape_dates_used    STRING           OPTIONS(description="Comma-separated collection dates actually aggregated. Plural because the two nights can legitimately come from two different collection days under the per-night alignment."),
  index_day_offset_days INT64           OPTIONS(description="Largest gap between a collection date used and the one the confirmed index day implies. Non-zero means rate drift has crept in."),
  n_properties_expected INT64           OPTIONS(description="Properties the pinned panel expected for this cell. Coverage = n_properties / n_properties_expected."),
  n_properties_churned  INT64           OPTIONS(description="Pinned properties that returned nothing this month -- closed, rebranded, or gone from the aggregator. The churn that matched-sample logic exists to absorb."),
  weights_are_placeholder BOOL          OPTIONS(description="TRUE if the aggregate used placeholder rather than real ONS weights. Such rows must never be reported as validated."),
  source_is_cached     BOOL             OPTIONS(description="TRUE if the underlying rates came from a cache-backed provider rather than live collection-day quotes."),
  board_basis_known    BOOL             OPTIONS(description="TRUE only if every contributing row carried a board basis. FALSE today for every row, because no implemented provider reports it. Kept as a column rather than a README footnote so the gap travels with the data."),
  rate_basis           STRING           OPTIONS(description="Cancellation policy the contributing rows were filtered to."),
  tax_basis            STRING           OPTIONS(description="Which price figure the contributing rows used."),

  pipeline_version     STRING           OPTIONS(description="Version of the reconstruction logic."),
  is_current           BOOL             OPTIONS(description="TRUE for the latest vintage of this (index_month, location, tier, night, and the four rule columns)."),
  run_id               STRING           OPTIONS(description="Groups rows written by a single reconciliation run.")
)
PARTITION BY index_month
CLUSTER BY location, attribution_rule, sample_rule
OPTIONS(
  description="Reconstructed ONS accommodation sub-indices, one vintage per computation, one row per methodology variant. Append-only."
);

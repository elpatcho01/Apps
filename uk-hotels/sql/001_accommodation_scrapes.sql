-- Append-only panel of forward-looking accommodation price observations.
--
-- INVARIANT: this table is never UPDATEd and never DELETEd from. Every pull is
-- a new vintage. If a rate looks wrong the fix is another row, not an edit --
-- the whole point is to be able to reconstruct what we believed on any past
-- date, which is impossible if history is mutable. A contaminated run stays in
-- the table as the evidence for whatever fix it prompted; queries exclude it
-- via the `accommodation_current_scrapes` view, they do not delete it.
--
-- GRAIN: one row per
--   (collection date x location x stay night x property).
-- Not per location: the provider returns every property in a location from one
-- call, and the index is built on property-level month-on-month relatives, so
-- collapsing to a location average at write time would throw away the matched
-- sample before it could be computed. A location call that fails or returns
-- nothing still writes exactly one row, with the property columns NULL and
-- `status` saying which -- an absent row and a failed row are different facts
-- and only one of them is recoverable.
--
-- Clustered on the columns reconciliation filters on: location and stay night
-- kind. Partitioned on collection date, which is what every query bounds.

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.accommodation_scrapes` (
  scrape_ts        TIMESTAMP NOT NULL OPTIONS(description="When we issued the query."),
  scrape_date      DATE      NOT NULL OPTIONS(description="Partition key. The collection date -- roughly six weeks BEFORE the stay, not the stay date. This is the largest single difference from the air fares panel and the cause of most confusion when reading it."),
  location         STRING    NOT NULL OPTIONS(description='ONS region code, e.g. "north_west". The dimension ONS publish this item on.'),
  city             STRING             OPTIONS(description="City standing in for the region, e.g. Manchester. Recorded so the proxy is auditable."),

  -- ---------------------------------------------------------------------
  -- What was priced
  -- ---------------------------------------------------------------------
  check_in         DATE      NOT NULL OPTIONS(description="Stay night. One of the two nights ONS sample: the index-week Tuesday, or the Thursday nine days later."),
  check_out        DATE      NOT NULL OPTIONS(description="check_in + 1. ONS price a single overnight stay; there is no multi-night pattern."),
  stay_night_kind  STRING             OPTIONS(description='"index_week" or "thursday_after". Which of the two sampled nights this is.'),
  advance_days     INT64              OPTIONS(description="Nominal advance window. 42 (six weeks) for the live 2026 item."),
  advance_days_actual INT64           OPTIONS(description="Real days between scrape_date and check_in. Equals advance_days under the per-night alignment; 51 for the Thursday under the single-day alignment; anything else means a run slipped."),
  collection_alignment STRING         OPTIONS(description='"per_night" or "single_day". Which reading of "collected six weeks in advance for two nights" this row was collected under. Genuinely ambiguous in the published wording, so both are collected and validation settles it.'),
  index_day        DATE               OPTIONS(description="The index day anchoring this stay (2nd or 3rd Tuesday). A hypothesis at collection time; the bulletin confirms it retrospectively."),
  index_day_ordinal INT64             OPTIONS(description="2 or 3 -- which Tuesday-of-month index_day is."),
  index_month_stay DATE               OPTIONS(description="First of the month the stay falls in. The stay-month attribution hypothesis."),
  index_month_collection DATE         OPTIONS(description="First of the month the price was collected. The collection-month attribution hypothesis. With a six-week lead these two differ by one or two whole months almost always, so choosing wrongly is not a boundary-case error."),

  -- ---------------------------------------------------------------------
  -- Property identity. Token, never name -- see the panel module.
  -- ---------------------------------------------------------------------
  property_token   STRING             OPTIONS(description="Provider-stable property identifier. The join key across months. NULL on error/no_data rows."),
  property_name    STRING             OPTIONS(description="Property name as returned. Informational only: a rebrand changes this and not the token, and matching on it would read a rebrand as a property leaving and another arriving."),
  property_tier    STRING             OPTIONS(description='"midscale" (3-3.5 star) or "upscale" (4-4.5 star). Our proxy for ONS property-class stratification, which is not public.'),
  hotel_class      NUMERIC            OPTIONS(description="Star rating as returned. NULL means unrated, which excludes the property from every tier."),
  property_type    STRING             OPTIONS(description='"hotel", "vacation_rental", etc. Non-hotel types are filtered out before selection; the raw value is kept so that decision is auditable.'),
  is_panel_property BOOL              OPTIONS(description="TRUE if this property is in the pinned sample in data/property_panel.csv. The pinned panel mirrors ONS's fixed property sample; the unpinned rows are the census that keeps the matched sample from silently shrinking."),

  -- ---------------------------------------------------------------------
  -- Price, stored under both tax bases because which one ONS record is not
  -- established and mixing them silently is a permanent, unrecoverable bias.
  -- ---------------------------------------------------------------------
  price_gbp        NUMERIC            OPTIONS(description="Headline nightly rate under the configured tax_basis. NULL when the query returned nothing."),
  price_before_taxes_gbp NUMERIC      OPTIONS(description="Same rate excluding taxes and fees, where the provider separates them. NULL means it did not, which is itself worth knowing."),
  price_cheapest_gbp NUMERIC          OPTIONS(description="Cheapest comparable property in the same (location, tier, night) cell. Cell-level diagnostic repeated on every row of the cell, so the gap between a panel property and the cheapest alternative is measurable rather than assumed. This diagnostic is what caught the equivalent selection bug on the air fares project."),
  tax_basis        STRING             OPTIONS(description='"advertised" or "before_taxes" -- which figure fills price_gbp.'),
  rate_basis       STRING             OPTIONS(description='Cancellation policy held constant: "free_cancellation", "non_refundable" or "any". Refundable and non-refundable rates for an identical room routinely differ by 30-40%, making this the single biggest contamination risk in the dataset.'),
  free_cancellation BOOL              OPTIONS(description="Whether the returned rate is free-cancellation. NULL means the provider did not say, and such rows are excluded unless rate_basis is 'any'."),
  board_basis      STRING             OPTIONS(description="Room-only / B&B / half board. ALWAYS NULL from every provider implemented so far -- Google Hotels does not expose it. A real gap in the comparability controls, represented explicitly rather than omitted."),
  room_type        STRING             OPTIONS(description="ALWAYS NULL for the same reason as board_basis."),
  adults           INT64              OPTIONS(description="Occupancy held constant at 2 across the whole series."),
  children         INT64              OPTIONS(description="Held constant at 0."),
  currency_raw     STRING             OPTIONS(description="Currency the provider actually returned."),

  -- ---------------------------------------------------------------------
  -- Comparability-filter diagnostics. What was excluded, and why.
  -- ---------------------------------------------------------------------
  comparability_basis STRING          OPTIONS(description='What the filter applied, e.g. "tier=upscale+rate=free_cancellation+board=unknown+room=unknown+outlier_capped". Recorded per row so the filtering is auditable rather than invisible. "board=unknown" appears on every row today and is a statement of what we could NOT control for.'),
  n_quotes         INT64              OPTIONS(description="Properties the provider returned for the location. 0 is a valid no-data observation."),
  n_considered     INT64              OPTIONS(description="Properties surviving the comparability filter in this row's tier. Compare with n_quotes to see how much was excluded."),
  n_dropped_rate_basis INT64          OPTIONS(description="Excluded because their cancellation policy did not match rate_basis. A high count means the cell is thinner than the property count suggests."),
  n_dropped_tier   INT64              OPTIONS(description="Excluded as unrated, below 3-star or above 4.5-star."),
  n_dropped_property_type INT64       OPTIONS(description="Excluded as vacation rentals or other non-hotel products."),
  n_dropped_outlier INT64             OPTIONS(description="Excluded by the price cap relative to the cheapest comparable in the tier."),
  cell_price_spread_ratio NUMERIC     OPTIONS(description="Dearest over cheapest within the comparable set. If this stays large after filtering, the set is not comparable and the filter needs another control -- not the index another caveat."),

  -- ---------------------------------------------------------------------
  -- Provenance
  -- ---------------------------------------------------------------------
  source_api       STRING    NOT NULL OPTIONS(description="Provider identifier, e.g. serpapi_google_hotels."),
  raw_response     JSON               OPTIONS(description="Full provider payload for the location call, for audit and reprocessing. Retained so observations can be re-scored under a different comparability rule without re-querying; this has already paid for itself once on the sibling project."),
  is_cached_source BOOL               OPTIONS(description="TRUE when the provider serves cached rates rather than a live availability call. A cached rate is not the measurement ONS make on collection day, and validation downgrades its verdict."),
  status           STRING             OPTIONS(description='"ok" | "no_data" | "error".'),
  error_message    STRING             OPTIONS(description="Populated when status = 'error'. The row is still written, because an absent row and a failed row are different facts."),
  pipeline_version STRING             OPTIONS(description="Version of the collection logic, so methodology changes are traceable across vintages."),
  run_id           STRING             OPTIONS(description="Groups all rows written by a single pipeline run. The unit `accommodation_current_scrapes` selects on.")
)
PARTITION BY scrape_date
CLUSTER BY location, stay_night_kind, property_tier
OPTIONS(
  description="Append-only, fully vintaged panel of forward-looking accommodation price observations, collected to mirror ONS CPI item class 11.2.0.1 methodology as it stands in 2026 (six weeks in advance, two weeknights per month). Never UPDATE or DELETE."
);

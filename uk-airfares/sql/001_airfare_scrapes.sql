-- Append-only panel of forward-looking fare observations.
--
-- INVARIANT: this table is never UPDATEd and never DELETEd from. Every pull is
-- a new vintage. If a price looks wrong, the fix is another row, not an edit --
-- the whole point is to be able to reconstruct what we believed on any past
-- date, which is impossible if history is mutable.
--
-- The columns after `index_month_hyp` are extensions beyond the original spec,
-- added to keep the panel faithful to ONS's published methodology (FOI-2023-1164
-- and Technical Manual s9.5.5). See the comments on each.

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.airfare_scrapes` (
  scrape_ts        TIMESTAMP NOT NULL OPTIONS(description="When we issued the query."),
  scrape_date      DATE      NOT NULL OPTIONS(description="Partition key. Collection date."),
  route            STRING    NOT NULL OPTIONS(description='e.g. "LHR-JFK".'),
  haul_category    STRING    NOT NULL OPTIONS(description='"domestic" | "european" | "long_haul".'),
  days_out         INT64     NOT NULL OPTIONS(description="Nominal advance window bucket: 30, 90 or 180. A label for the 1/3/6-month window, not a literal day count -- see days_out_actual."),
  departure_date   DATE      NOT NULL OPTIONS(description="Outbound departure. Targeted at the index day (nth Tuesday) of the month 1/3/6 months ahead, not scrape_date + N days."),
  return_date      DATE               OPTIONS(description="Return leg: +1wk domestic, +2wks European, +3wks long-haul. ONS price a return, so this is normally populated."),
  price_gbp        NUMERIC            OPTIONS(description="Headline fare, ONS selection rule (flight departing closest to the fixed target time). NULL when the query returned no quotes."),
  currency_raw     STRING             OPTIONS(description="Currency the provider actually returned."),
  source_api       STRING    NOT NULL OPTIONS(description="Provider identifier, e.g. travelpayouts_aviasales_v3."),
  raw_response     JSON               OPTIONS(description="Full provider payload, for audit and reprocessing."),
  index_month_hyp  DATE               OPTIONS(description="Hypothesised ONS index month this observation feeds, under the departure-month attribution rule. Equals index_month_departure; kept under the original spec name."),

  -- ---------------------------------------------------------------------
  -- Methodology-fidelity extensions
  -- ---------------------------------------------------------------------
  days_out_actual  INT64              OPTIONS(description="Real calendar days between scrape_date and departure_date. An index-day-to-index-day '1 month' window is 28-35 days, never exactly 30."),
  months_ahead     INT64              OPTIONS(description="Advance window in whole months: 1, 3 or 6. The unambiguous form of days_out."),
  index_day_ordinal INT64             OPTIONS(description="Which Tuesday-of-month the departure targets (2 or 3). Our index-day hypothesis for this row, before ONS confirm it."),

  -- Attribution is genuinely ambiguous from published sources: a fare could be
  -- attributed to the month it departs, or to the month it was collected. Both
  -- are stored so that Task 6 validation can score the two hypotheses against
  -- ONS's published sub-indices rather than us guessing now and baking it in.
  index_month_departure  DATE         OPTIONS(description="First of the month the flight departs."),
  index_month_collection DATE         OPTIONS(description="First of the month the price was collected."),

  -- Selection-rule diagnostics. ONS take the flight nearest a fixed target
  -- time, not the cheapest; storing both makes the gap measurable.
  price_cheapest_gbp NUMERIC          OPTIONS(description="Cheapest fare for the same query regardless of departure time."),
  ons_rule_time_delta_minutes INT64   OPTIONS(description="Minutes between the selected flight's departure and the target time. NULL means no quote carried a usable departure time, so the ONS rule could not truly be applied to this row."),
  target_departure_time STRING        OPTIONS(description="The fixed target time-of-day used, e.g. '09:00'. Held constant across months."),
  n_quotes         INT64              OPTIONS(description="How many candidate quotes the provider returned. 0 is a valid no-data observation."),

  -- Provenance and data-quality flags.
  selected_airline STRING             OPTIONS(description="Carrier of the selected itinerary."),
  selected_departure_ts TIMESTAMP     OPTIONS(description="Actual departure datetime of the selected itinerary."),
  quote_found_at   TIMESTAMP          OPTIONS(description="When the provider last observed this price. For cache-backed sources this can be well before scrape_ts."),
  is_cached_source BOOL               OPTIONS(description="TRUE when the provider serves cached prices rather than a live quote. Travelpayouts returns fares found by users in the preceding 48h, which is NOT the same measurement ONS make on index day."),
  status           STRING             OPTIONS(description='"ok" | "no_data" | "error".'),
  error_message    STRING             OPTIONS(description="Populated when status = 'error'; the row is still written so failures are visible in the panel rather than silently absent."),
  run_id           STRING             OPTIONS(description="Groups all rows written by a single pipeline run.")
)
PARTITION BY scrape_date
CLUSTER BY haul_category, route
OPTIONS(
  description="Append-only, fully vintaged panel of forward-looking air fare observations, collected to mirror ONS CPI item 07.3.3 methodology. Never UPDATE or DELETE."
);

-- Migration: record how the candidate set was filtered before selection.
--
-- WHY: on the first day of live collection the ONS target-time rule -- which is
-- price-blind by design -- selected connecting itineraries that Google Flights
-- lists alongside direct services:
--
--     LGW-EDI  £4,841  SWISS       09:25   (cheapest direct £72)
--     LHR-ABZ  £3,215  Air France  08:55   (cheapest direct £135)
--
-- SWISS does not fly Gatwick to Edinburgh; that is LGW-Zurich-EDI. Selection
-- now filters to comparable products first, and these columns record what that
-- filter did so the decision is auditable per row rather than invisible.

ALTER TABLE `${PROJECT}.${DATASET}.airfare_scrapes`
  ADD COLUMN IF NOT EXISTS n_quotes_considered INT64
  OPTIONS(description="Candidates left after filtering to comparable products. Compare with n_quotes to see how many the provider returned in total.");

ALTER TABLE `${PROJECT}.${DATASET}.airfare_scrapes`
  ADD COLUMN IF NOT EXISTS candidate_basis STRING
  OPTIONS(description='How the candidate set was narrowed: "direct_only" | "no_direct_available", optionally suffixed "+outlier_capped". "no_direct_available" means the route/date had no non-stop service and connections were used instead.');

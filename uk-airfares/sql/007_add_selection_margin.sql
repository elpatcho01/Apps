-- How much further from the target time the runner-up candidate sat.
--
-- Added after four consecutive collection days (2026-08-17..20) showed the
-- long-haul cells moving 4.5-7.2% day to day while the price-blind cheapest
-- fare on the same queries moved 0.6-2.1%. The market was nearly still. The
-- movement was the selection rule flipping between two candidates:
-- ons_rule_time_delta_minutes oscillated 173 -> 273 -> 173 -> 273, while the
-- short-haul cells sat flat at 59 and 66.
--
-- That was only visible by comparing days in aggregate. Nothing on an individual
-- row said "this choice was a coin flip". This column does: a SMALL margin means
-- one flight entering or leaving the provider's result set would have changed
-- the answer, a LARGE margin means the winner was unambiguous, and NULL means
-- there was no runner-up at all -- a single timed candidate, which is its own
-- kind of fragile and worth telling apart from a confident wide margin.
--
-- Nullable and additive, so every row already written stays valid and simply
-- carries NULL.
ALTER TABLE `${PROJECT}.${DATASET}.airfare_scrapes`
  ADD COLUMN IF NOT EXISTS selection_margin_minutes INT64
  OPTIONS(description="Minutes by which the runner-up candidate was FURTHER from the target departure time than the selected one. Small means the selection was a near-tie and fragile to the provider's result set changing; NULL means there was no runner-up.");

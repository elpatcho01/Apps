-- The matched-sample price relative, which is the number a nowcast is actually made of.
--
-- WHY THIS IS A CORRECTION, NOT AN ADDITION
--
-- index.py has always implemented matched_pairs, price_relative (Jevons, Dutot,
-- Carli) and splice_nowcast, with a test proving the naive alternative invents a
-- 25% price collapse out of one dropped route. Nothing called it. Reconciliation
-- stored an unmatched monthly LEVEL and validate.py derived month-on-month
-- changes from those levels -- so the protection was built, documented as
-- load-bearing, and not in the path.
--
-- The difference matters whenever the basket changes between months. If LHR-CPT
-- prices at £900 in one month and returns nothing the next, an unmatched average
-- reads the absence as a fall in fares when nothing about the market moved. CPI
-- computes price relatives on matched models for exactly this reason.
--
-- Current exposure is narrow but real: european 3m already prices 8 routes on
-- some days and 9 on others, and LGW-NAP 3m fails more often than it succeeds.
-- Long-haul happens to be stable at 6 routes today, which is luck rather than
-- design.
--
-- WHAT IS STORED AND WHY IT IS ADDITIVE
--
-- reconstructed_value keeps its meaning -- the level in pounds for this month --
-- so nothing already written changes interpretation. These columns carry the
-- matched relative ALONGSIDE it, against the previous index month for the same
-- (haul, window, selection rule). Validation prefers the stored relative and
-- falls back to differencing levels only where one could not be computed, which
-- keeps every row already in the table usable.
--
-- The formula follows the aggregation it sits beside, because they are the same
-- question asked two ways: an arithmetic mean of levels pairs with Dutot (ratio
-- of means), a geometric mean with Jevons (geometric mean of relatives). Median
-- has no standard elementary analogue, so its relative is NULL rather than
-- invented.
--
-- n_matched_routes and n_unmatched_routes are stored because the relative is
-- only as trustworthy as its overlap: three matched routes out of nine is a
-- different number from nine out of nine, and a reader must be able to tell.
ALTER TABLE `${PROJECT}.${DATASET}.reconstructed_index`
  ADD COLUMN IF NOT EXISTS price_relative NUMERIC
  OPTIONS(description="Matched-sample price relative from the previous index month to this one, computed over routes priced in BOTH months. 1.05 means a 5% rise. NULL where there is no previous month, too few matched routes, or no standard formula for the aggregation.");

ALTER TABLE `${PROJECT}.${DATASET}.reconstructed_index`
  ADD COLUMN IF NOT EXISTS relative_formula STRING
  OPTIONS(description="Elementary aggregate formula behind price_relative: 'jevons' (geometric mean of relatives, the CPI default) or 'dutot' (ratio of arithmetic means). NULL when no relative was computed.");

ALTER TABLE `${PROJECT}.${DATASET}.reconstructed_index`
  ADD COLUMN IF NOT EXISTS n_matched_routes INT64
  OPTIONS(description="Routes priced in both this index month and the previous one, and therefore used in price_relative.");

ALTER TABLE `${PROJECT}.${DATASET}.reconstructed_index`
  ADD COLUMN IF NOT EXISTS n_unmatched_routes INT64
  OPTIONS(description="Routes priced in one of the two months but not the other, and therefore excluded from price_relative. A high count means the relative rests on a shrinking basket.");

ALTER TABLE `${PROJECT}.${DATASET}.reconstructed_index`
  ADD COLUMN IF NOT EXISTS prev_index_month DATE
  OPTIONS(description="The index month price_relative is measured from. Stored rather than assumed to be month-minus-one, because a gap in collection makes the previous AVAILABLE month the honest base.");

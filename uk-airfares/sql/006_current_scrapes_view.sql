-- A view exposing one coherent vintage per collection date.
--
-- WHY THIS EXISTS INSTEAD OF DELETING ROWS
--
-- On 2026-08-17 three runs landed for the same date: two carried a selection bug
-- (connecting itineraries priced at 20-70x the direct fare were being chosen)
-- and the third was clean. The obvious response is to delete the bad two. It is
-- the wrong response, for three reasons:
--
--   1. `airfare_scrapes` is append-only by design, and the value of that
--      guarantee is that it is unconditional. Once "obviously wrong" rows get
--      deleted, the panel is mutable and every past figure becomes a claim
--      rather than a record. The guarantee is worth more than the tidiness.
--   2. Those rows are the evidence for why the direct-flight filter exists. They
--      are the only observation of the failure, and it is not reproducible --
--      those fares are gone.
--   3. Reconstruction already ignores them: reconcile takes the latest run for a
--      date, so they cannot reach a reconstructed index.
--
-- The real problem was never their presence -- it was that a naive query
-- averaged across all three runs and got a misleading answer. That is a query
-- problem, so this is a query fix.
--
-- Use `current_scrapes` for analysis and `airfare_scrapes` for audit. The view's
-- definition deliberately matches `reconcile.PANEL_QUERY`: latest *run* per
-- date, not latest row per observation, so a run is always read as the coherent
-- snapshot it was collected as.

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.current_scrapes` AS
WITH latest_run AS (
  SELECT scrape_date, run_id
  FROM (
    SELECT
      scrape_date,
      run_id,
      ROW_NUMBER() OVER (PARTITION BY scrape_date ORDER BY scrape_ts DESC) AS rn
    FROM (SELECT DISTINCT scrape_date, run_id, scrape_ts FROM `${PROJECT}.${DATASET}.airfare_scrapes`)
  )
  WHERE rn = 1
)
SELECT s.*
FROM `${PROJECT}.${DATASET}.airfare_scrapes` AS s
JOIN latest_run AS l
  ON s.scrape_date = l.scrape_date AND s.run_id = l.run_id;

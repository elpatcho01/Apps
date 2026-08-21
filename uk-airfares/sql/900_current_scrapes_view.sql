-- NUMBERED 900 SO IT ALWAYS SORTS LAST. This is not cosmetic.
--
-- `ensure_tables` applies sql/*.sql in filename order, and this view is defined
-- as `SELECT s.*`. BigQuery resolves that star AT CREATION TIME and freezes the
-- column list into the view definition -- it does not re-expand on read. So a
-- view created before a migration that adds a column simply does not have that
-- column, and stays that way until it is recreated.
--
-- As 006 that broke on the very next migration: 006 recreated the view, THEN 007
-- added selection_margin_minutes, so the view was a full run behind and the new
-- column was invisible to the digest, the export and the dashboard. The symptom
-- would have been a column that exists in the table and is missing from every
-- consumer -- exactly the kind of absence this project has already been caught by
-- once, with current_scrapes returning NotFound because the DDL had not caught up.
--
-- Views depend on tables, so views run after tables. The 900 band is reserved for
-- them; put future migrations in the 0xx band and future views here.

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

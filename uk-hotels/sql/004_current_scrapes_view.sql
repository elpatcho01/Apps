-- A view exposing one coherent vintage per collection date.
--
-- WHY THIS EXISTS INSTEAD OF DELETING ROWS
--
-- A collection date can legitimately carry several runs: a retry after a
-- partial failure, a manual re-run, a double-clicked dispatch. On the sibling
-- air fares project three runs landed for one date, two of them carrying a
-- selection bug, and the obvious response -- delete the bad two -- was the
-- wrong one, for three reasons that apply identically here:
--
--   1. `accommodation_scrapes` is append-only by design, and the value of that
--      guarantee is that it is unconditional. Once "obviously wrong" rows get
--      deleted the panel is mutable and every past figure becomes a claim
--      rather than a record.
--   2. Those rows are the evidence for whatever fix they prompted. They are the
--      only observation of the failure, and it is not reproducible -- the rates
--      are gone.
--   3. Reconstruction already ignores them: reconcile takes the latest run per
--      date, so they cannot reach a reconstructed index.
--
-- The real problem was never their presence. It was that a naive query averaged
-- across all runs and got a misleading answer. That is a query problem, so this
-- is a query fix.
--
-- Use `accommodation_current_scrapes` for analysis and `accommodation_scrapes` for audit.
-- The definition deliberately matches `reconcile.PANEL_QUERY`: latest *run* per
-- date, not latest row per observation, so a run is always read as the coherent
-- snapshot it was collected as. A run that priced eleven of twelve regions
-- before failing supersedes an earlier complete one, which is the correct
-- behaviour -- it is the more recent belief -- and the missing region shows up
-- as reduced coverage rather than as a silent blend of two vintages.

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.accommodation_current_scrapes` AS
WITH latest_run AS (
  SELECT scrape_date, run_id
  FROM (
    SELECT
      scrape_date,
      run_id,
      ROW_NUMBER() OVER (PARTITION BY scrape_date ORDER BY scrape_ts DESC) AS rn
    FROM (
      SELECT DISTINCT scrape_date, run_id, scrape_ts
      FROM `${PROJECT}.${DATASET}.accommodation_scrapes`
    )
  )
  WHERE rn = 1
)
SELECT s.*
FROM `${PROJECT}.${DATASET}.accommodation_scrapes` AS s
JOIN latest_run AS l
  ON s.scrape_date = l.scrape_date AND s.run_id = l.run_id;

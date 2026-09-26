-- Latest vintage of each output. Analysis reads these; audit reads the tables.
CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.current_exposure` AS
SELECT * EXCEPT (rn) FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY origin_month, target_month ORDER BY computed_ts DESC) AS rn
  FROM `${PROJECT}.${DATASET}.exposure`)
WHERE rn = 1;

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.current_reconstruction` AS
SELECT * EXCEPT (rn) FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY month, variant ORDER BY computed_ts DESC) AS rn
  FROM `${PROJECT}.${DATASET}.reconstruction`)
WHERE rn = 1;

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.current_ons_series` AS
SELECT * EXCEPT (rn) FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY cdid, period, frequency ORDER BY fetched_ts DESC) AS rn
  FROM `${PROJECT}.${DATASET}.ons_series`)
WHERE rn = 1;

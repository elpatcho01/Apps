-- Workstream 3: index-day nowcasts of CHBJ. Written when a panel vendor is
-- live; the validation rows (published value, error) arrive as later rows for
-- the same target month rather than as UPDATEs.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.nowcasts` (
  target_month DATE NOT NULL,
  index_day DATE NOT NULL,
  index_day_source STRING NOT NULL OPTIONS (description = "predicted (Tuesday nearest the 13th) | confirmed (bulletin)"),
  method STRING NOT NULL,
  level FLOAT64 NOT NULL,
  mom_pct FLOAT64,
  yoy_pct FLOAT64,
  headline_contribution_bp FLOAT64,
  sd_pct FLOAT64,
  panel_vendor STRING,
  panel_obs_date DATE,
  calibration_n INT64,
  published_level FLOAT64 OPTIONS (description = "Filled on a later row once CHBJ publishes"),
  error_pct FLOAT64,
  pipeline_version STRING NOT NULL,
  computed_ts TIMESTAMP NOT NULL
)
PARTITION BY target_month
OPTIONS (description = "WS3 nowcast vintages. Never UPDATE or DELETE.");

-- Workstream 1: forward fixing exposure. One row per (origin, target month)
-- per computation; clothing's contribution to the RPI all-items MoM in bp,
-- exact chain-linked formula, with the fan candidate that produced it.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.exposure` (
  origin_month DATE NOT NULL OPTIONS (description = "Last published CHBJ month the forecast is made from"),
  target_month DATE NOT NULL,
  horizon INT64 NOT NULL,
  expected_bp FLOAT64,
  sd_bp FLOAT64,
  p10_bp FLOAT64,
  p50_bp FLOAT64,
  p90_bp FLOAT64,
  effective_weight_ppt FLOAT64,
  nominal_weight_ppt FLOAT64,
  weight_carried_forward BOOL OPTIONS (description = "Next year's CZHJ not yet published; latest weight used"),
  variance_candidate STRING NOT NULL,
  point_model STRING NOT NULL,
  pipeline_version STRING NOT NULL,
  computed_ts TIMESTAMP NOT NULL
)
PARTITION BY target_month
OPTIONS (description = "WS1 exposure table vintages. Never UPDATE or DELETE.");

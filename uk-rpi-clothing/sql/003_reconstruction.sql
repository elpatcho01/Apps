-- Workstream 2a: CHBJ rebuilt from ONS price quotes, per month and variant,
-- with the error against published CHBJ, the bottom-up formula wedge, and
-- dispersion. Append-only; computed_ts orders vintages.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.reconstruction` (
  month DATE NOT NULL,
  variant STRING NOT NULL OPTIONS (description = "Elementary-aggregate definition, e.g. 1dutot_2carli+shopw"),
  weight_basis STRING OPTIONS (description = "rpi (2026+) | cpi_proxy (earlier: CPI item weights within RPI sub-sections)"),
  n_items INT64,
  n_quotes INT64,
  section_rpi_index FLOAT64 OPTIONS (description = "Jan=100 within chain year"),
  section_jevons_index FLOAT64 OPTIONS (description = "Same quotes, same weights, Jevons"),
  chbj_published FLOAT64,
  chbj_reconstructed FLOAT64,
  error_pct FLOAT64,
  formula_wedge_yoy_pp FLOAT64 OPTIONS (description = "12m log change, chained Carli minus chained Jevons"),
  observed_wedge_yoy_pp FLOAT64 OPTIONS (description = "12m log change, CHBJ minus D7BW"),
  var_log FLOAT64,
  sale_share FLOAT64,
  pipeline_version STRING NOT NULL,
  computed_ts TIMESTAMP NOT NULL
)
PARTITION BY DATE_TRUNC(month, YEAR)
CLUSTER BY variant
OPTIONS (description = "WS2a reconstruction vintages. Never UPDATE or DELETE.");

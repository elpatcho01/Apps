-- ONS published series used by the pipeline (CHBJ, D7BW, CHAW, CZFY, the
-- DOCK-DOCO sub-sections, CZHJ and CZXY-CZYC weights). Append-only and
-- vintaged: RPI is never revised, but CPI and weights can be, and a value we
-- scored against must never be silently rewritten. fetched_ts orders vintages.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.ons_series` (
  cdid STRING NOT NULL,
  period DATE NOT NULL OPTIONS (description = "First of the month; 1 January for annual values"),
  frequency STRING NOT NULL OPTIONS (description = "monthly | annual"),
  value FLOAT64 NOT NULL,
  title STRING,
  source_url STRING NOT NULL OPTIONS (description = "Always the cy.ons.gov.uk mirror; recorded so a mixed history is visible"),
  fetched_ts TIMESTAMP NOT NULL
)
PARTITION BY DATE_TRUNC(period, YEAR)
CLUSTER BY cdid
OPTIONS (description = "ONS published series, vintaged. Never UPDATE or DELETE.");

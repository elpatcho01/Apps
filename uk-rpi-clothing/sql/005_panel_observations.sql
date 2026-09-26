-- Workstream 2b: licensed vendor panel, SKU level. Never exported to git (the
-- licence forbids it and git keeps everything forever); only aggregates leave.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.panel_observations` (
  obs_date DATE NOT NULL,
  vendor STRING NOT NULL,
  fascia STRING NOT NULL,
  sku STRING NOT NULL,
  vendor_category STRING NOT NULL,
  full_price FLOAT64,
  price FLOAT64,
  on_markdown BOOL,
  raw_payload STRING OPTIONS (description = "Vendor record as delivered, for reprocessing"),
  load_ts TIMESTAMP NOT NULL
)
PARTITION BY obs_date
CLUSTER BY vendor, vendor_category
OPTIONS (description = "Licensed SKU panel. Append-only. Mock data must never land here.");

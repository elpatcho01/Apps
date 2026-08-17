-- Migration: add `months_ahead` to the two index tables.
--
-- WHY THIS FILE EXISTS
-- The first production run of the backfill dumped the real ONS workbook and
-- revealed that ONS publish each haul category broken out by advance window --
-- domestic at 1 month, European at 1 and 3, long-haul at 1, 3 and 6. Six
-- published series, not three. Our panel already collects at that granularity,
-- so reconstructions and the published target both need the window as part of
-- their key to be comparable.
--
-- The tables in 002/003 are created with CREATE TABLE IF NOT EXISTS, which does
-- nothing to a table that already exists. Anyone who ran ensure_tables before
-- this change has the old shape, and a load job against it would fail on an
-- unknown field. ADD COLUMN IF NOT EXISTS is idempotent in both directions: it
-- adds the column where missing and is a no-op where present, so no table needs
-- dropping and no data is lost.
--
-- Adding a nullable column to a BigQuery table is metadata-only -- existing rows
-- read back NULL for it, which is the correct value for anything written before
-- the window was tracked.

ALTER TABLE `${PROJECT}.${DATASET}.ons_published_index`
  ADD COLUMN IF NOT EXISTS months_ahead INT64
  OPTIONS(description="Advance window the series is for: 1, 3 or 6 months.");

ALTER TABLE `${PROJECT}.${DATASET}.reconstructed_index`
  ADD COLUMN IF NOT EXISTS months_ahead INT64
  OPTIONS(description="Advance window: 1, 3 or 6 months. Matches ONS's published granularity.");

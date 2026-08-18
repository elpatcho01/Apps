-- Property churn, made visible.
--
-- WHY A VIEW RATHER THAN A COLUMN
--
-- "This property left the sample" is not a fact about any single row -- it is a
-- fact about a property's absence from a later month, which no row can carry.
-- The air fares panel needs nothing like this because a route does not stop
-- existing; a hotel closes, rebrands, renovates into a different price tier, or
-- simply stops being listed, and each of those looks exactly like a price
-- movement to an unmatched average.
--
-- So churn is computed rather than recorded, and the digest reads it every
-- month. A property whose last_seen is two months old is either gone or the
-- collector is broken, and those are opposite situations that look identical
-- from a single month's data.

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.accommodation_property_churn` AS
WITH observed AS (
  SELECT
    location,
    property_tier,
    property_token,
    ANY_VALUE(property_name)  AS property_name,
    ANY_VALUE(is_panel_property) AS is_panel_property,
    MIN(index_month_stay)     AS first_month,
    MAX(index_month_stay)     AS last_month,
    COUNT(DISTINCT index_month_stay) AS months_seen,
    COUNT(*)                  AS observations
  FROM `${PROJECT}.${DATASET}.accommodation_current_scrapes`
  WHERE status = 'ok' AND property_token IS NOT NULL
  GROUP BY location, property_tier, property_token
),
panel_span AS (
  SELECT MAX(index_month_stay) AS latest_month
  FROM `${PROJECT}.${DATASET}.accommodation_current_scrapes`
  WHERE status = 'ok'
)
SELECT
  o.*,
  p.latest_month,
  DATE_DIFF(p.latest_month, o.last_month, MONTH) AS months_absent,
  -- "present" and "left" are the only two states worth naming. A property seen
  -- in the latest month is present; anything else has been absent for at least
  -- one full month, which is already enough to break a matched pair.
  IF(o.last_month = p.latest_month, 'present', 'left') AS churn_status,
  -- The share of months a property was actually priced in. A low value on a
  -- pinned panel property means intermittent availability rather than churn --
  -- the hotel was full on collection day, which is exactly the problem ONS's
  -- own move to six-weeks-ahead collection was made to solve.
  SAFE_DIVIDE(
    o.months_seen,
    DATE_DIFF(p.latest_month, o.first_month, MONTH) + 1
  ) AS presence_rate
FROM observed AS o
CROSS JOIN panel_span AS p;

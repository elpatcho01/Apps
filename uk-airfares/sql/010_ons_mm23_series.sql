-- ONS's own monthly published series, straight from the mm23 dataset.
--
-- WHY THIS EXISTS, AND WHY IT IS NOT ons_published_index
--
-- ons_published_index holds the six air fare SUB-indices from an ad hoc release:
-- domestic/European/long-haul by advance window, rebased to January = 100 every
-- year, and last published to February 2026. Two things follow from that shape,
-- and both of them cost us an answer already.
--
-- 1. The annual rebase severs year-on-year. An index that resets to 100 each
--    January cannot produce an annual rate, so the sub-indices can describe
--    seasonality and nothing else. Every question about the published annual
--    rate had to be answered from bulletin prose.
-- 2. The ad hoc release is annual and lags. Asked what August 2026 printed, the
--    honest answer from our own data was "we do not have it" -- while ONS had
--    published it on 16 September in a monthly series we simply were not reading.
--
-- mm23 carries the headline monthly series continuously and without rebasing:
-- the CPI air fares index and its monthly and annual rates, the CPI weight, and
-- the RPI fares group with its subgroups. So this table is what lets the project
-- answer a question about a published number by querying rather than searching.
--
-- WHY THE RPI SERIES ARE HERE TOO
--
-- RPI has no standalone air fares series: air sits inside "other travel costs"
-- (DOCY), itself inside "fares and other travel costs" (CHBR), alongside rail
-- and bus fares. That dilution is survivable for one specific reason -- rail and
-- bus fares are administered and barely move mid-year, so the September move in
-- these series is very largely an air fares signal. And RPI runs from January
-- 1987 against CPI's 2001, which matters because every interval this project
-- quotes for a September step is limited by having seen only 19 of them.
--
-- RPI is NOT a leading indicator and must not be used as one: both measures are
-- compiled from the same price quotes and published in the same release on the
-- same day. What it offers is history, and a way to size the formula effect --
-- RPI aggregates arithmetically where CPI uses a geometric mean, which is the
-- entire difference between them given identical inputs.
--
-- Append-only and vintaged, like ons_published_index: ONS revise, and a revision
-- must not overwrite the value we scored against last month.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.ons_mm23_series` (
  period          DATE      NOT NULL OPTIONS(description="Partition key. First of the month the observation belongs to."),
  cdid            STRING    NOT NULL OPTIONS(description="ONS four-character series identifier, upper case, e.g. 'D7EH'. The natural key alongside period."),
  value           NUMERIC            OPTIONS(description="Published value. An index level, a percentage change or a weight depending on the series -- read `kind`."),
  kind            STRING             OPTIONS(description="What `value` means: 'index' | 'monthly_rate' | 'annual_rate' | 'weight'. Stored so a consumer cannot accidentally average an index with a percentage."),
  measure         STRING             OPTIONS(description="'cpi' or 'rpi'. The two are compiled from the same price quotes but aggregate differently, so they must never be pooled without saying which is which."),
  series_label    STRING             OPTIONS(description="ONS's own title for the series, kept verbatim so a surprising number can be traced to what it actually measures."),
  basis           STRING             OPTIONS(description="Reference base as ONS state it, e.g. '2015=100' or 'Jan 1987=100'. NULL for rates and weights."),
  source_url      STRING             OPTIONS(description="The mm23 endpoint this vintage came from."),
  fetched_ts      TIMESTAMP          OPTIONS(description="When we retrieved it. Orders revision vintages."),
  is_current      BOOL               OPTIONS(description="TRUE for the latest vintage of this (period, cdid)."),
  run_id          STRING             OPTIONS(description="Groups rows written by a single fetch run.")
)
PARTITION BY period
CLUSTER BY cdid, measure
OPTIONS(
  description="ONS mm23 monthly series: CPI air fares (07.3.3) index, monthly rate, annual rate and weight, plus the RPI fares group and its subgroups. The published record, fetched rather than quoted. Append-only; revisions arrive as new vintages."
);

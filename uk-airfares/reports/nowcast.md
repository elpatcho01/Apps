# RPI fares and other travel: nowcast

Month-on-month change in CHBR. Last published month: **Oct 2026** (index 624.6).

Inputs: export generated 2026-11-02T16:46:36.289998+00:00; panel to 2026-10-26; mm23 from mm23_from_ons_upload_2026-10-21.json (--mm23); mm23 to 2026-09; hand-supplied CZFN:2026-10=1.6; index-day overrides none

| Month | Index day | Statistical | Scraping (basket seen) | Policy | **Forecast** | 80% range | Backtest ±2pp / ±5pp | Level | y/y |
|---|---|---|---|---|---|---|---|---|---|
| Nov 2026 | 10 Nov | -10.4 | +0.9 (34%) | — | **-9.4%** | -11.7 to -7.2 | 11/14 · 14/14 | 565.7 | +4.0% |
| Dec 2026 | 15 Dec | +28.8 | +0.0 (0%) | — | **+28.8%** | +24.6 to +33.1 | 8/14 · 12/14 | 728.8 | +18.1% |
| Jan 2027 | 12 Jan | -20.3 | +0.0 (0%) | -1.3 | **-21.6%** | -25.4 to -17.8 | 8/15 · 15/15 | 571.3 | +8.8% |
| Feb 2027 | 09 Feb | +3.6 | +0.0 (0%) | — | **+3.6%** | +2.7 to +4.4 | 15/15 · 15/15 | 591.9 | +7.5% |
| Mar 2027 | 09 Mar | +3.2 | +0.0 (0%) | -0.2 | **+3.0%** | -0.2 to +6.3 | 9/15 · 14/15 | 609.7 | +6.0% |
| Apr 2027 | 13 Apr | -3.2 | +0.6 (22%) | — | **-2.6%** | -6.1 to +1.0 | 9/15 · 14/15 | 594.0 | +4.2% |

## How each month was built

- **Nov 2026** — air fares -19.3% (median of 2025, 2024, 2023) × sensitivity 0.541 + non-air +0.1pp (median of 2025, 2024, 2023) = -10.4%.
  - scraped domestic 1m: -0.7% against a norm of -12.5% (weight 0.033, days 4/9)
  - scraped european 1m: -9.1% against a norm of -25.2% (weight 0.239, days 4/9)
  - scraped long_haul 1m: -16.5% against a norm of -4.3% (weight 0.065, days 4/9)
- **Dec 2026** — air fares +57.1% (median of 2023, 2022, 2017) × sensitivity 0.541 + non-air -2.0pp (median of 2025, 2024, 2023) = +28.8%.
  - scraping: no cell priced on both index days of this step
- **Jan 2027** — air fares -38.9% (median of 2024, 2023, 2018) × sensitivity 0.541 + non-air +0.7pp (median of 2026, 2025, 2024) = -20.3%.
  - scraping: no cell priced on both index days of this step
  - policy -1.3pp: GBP2 single bus fare cap in England from 1 January 2027, down from GBP3: bus index about -7% x bus share of the group about 0.18. Precedents: January 2023 (GBP2 cap introduced) bus -19.3% against a normal ~-6%; January 2025 (cap raised to GBP3) bus +0.9%. Source: DfT announcement of 22 July 2026, via secondary coverage (gov.uk not reached).
- **Feb 2027** — air fares +6.5% (median of 2026, 2025, 2024) × sensitivity 0.541 + non-air +0.1pp (median of 2026, 2025, 2024) = +3.6%.
  - scraping: no cell priced on both index days of this step
- **Mar 2027** — air fares +5.3% (median of 2024, 2016, 2013, 2008, 2005) × sensitivity 0.541 + non-air +0.3pp (median of 2026, 2025, 2024) = +3.2%.
  - scraping: no cell priced on both index days of this step
  - policy -0.2pp: Regulated rail fares frozen until March 2027 with nothing decided after it (as of October 2026). The non-air March baseline carries rises of ~0.3-0.5pp from 2024-25; this is the midpoint of a freeze (-0.4) and an RPI-linked rise (0). Source: National Rail fares freeze page; DfT.
- **Apr 2027** — air fares -6.4% (median of 2024, 2016, 2013, 2008, 2005) × sensitivity 0.541 + non-air +0.3pp (median of 2026, 2025, 2024) = -3.2%.
  - scraped long_haul 6m: +10.5% against a norm of +0.1% (weight 0.215, days 4/9)

## Backtest, 2010 onwards (each year forecast from earlier years only)

| Month | Route used | Years | Typical error | Bias | Within ±2pp | Within ±5pp | Air route | Copying RPI moves |
|---|---|---|---|---|---|---|---|---|
| Nov | air | 14 | 1.76pp | -0.50pp | 11/14 | 14/14 | 1.76pp | 2.11pp |
| Dec | air | 14 | 3.32pp | +0.69pp | 8/14 | 12/14 | 3.32pp | 5.86pp |
| Jan | air | 15 | 2.87pp | -1.37pp | 8/15 | 15/15 | 2.87pp | 5.29pp |
| Feb | air | 15 | 0.66pp | +0.11pp | 15/15 | 15/15 | 0.66pp | 0.73pp |
| Mar | air | 15 | 2.53pp | +0.36pp | 9/15 | 14/15 | 2.53pp | 2.63pp |
| Apr | air | 15 | 2.77pp | +0.36pp | 9/15 | 14/15 | 2.77pp | 3.56pp |

## Scraping record

Reliability applied: **0.50** — prior (1 of 6 months scored).

| Month | Actual | Statistical | With scraping | Basket seen |
|---|---|---|---|---|
| Oct 2026 | +1.6% | +0.9% | +1.3% | 34% |

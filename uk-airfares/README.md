# UK Air Fares Nowcasting Pipeline

Reconstructs the ONS domestic / European / long-haul air fare sub-indices
(CPI item 07.3.3, "Passenger transport by air") ahead of publication — not by
forecasting fares, but by capturing the same forward-looking price snapshots ONS
collect, at the same lead times, on the same days.

Data lands in BigQuery as an append-only, fully vintaged panel. Orchestration is
GitHub Actions cron. No airline or OTA websites are scraped.

---

## Read this first: what this pipeline can and cannot currently tell you

Three limitations are structural, not bugs, and they bound every number that
comes out of it.

**1. The fare source is a cache, not a live quote.** Travelpayouts' Aviasales
endpoint returns *"the cheapest tickets for specific dates found by Aviasales
users in the last 48 hours"*. ONS price collectors read the fare advertised on
index day. These are different measurements. Every row carries
`is_cached_source = TRUE` and the provider's own `quote_found_at`, and the
validation report downgrades any verdict built on cached data to `PROVISIONAL`.
Swapping to a live-pricing provider is a single adapter file — see
[Changing the fare source](#changing-the-fare-source).

**2. Coverage will be patchy on thin routes and distant dates.** Because the
cache is demand-driven, a specific Tuesday six months out on, say, LHR–CPT may
return nothing at all. That is recorded as a `no_data` row, not an error, so
gaps are visible in the panel rather than silently absent. Expect long-haul
180-day coverage to be the weakest cell in the table.

**3. Weights are fetched at runtime, and the parser is unverified.**
`ukairfares.onsweights` downloads the ONS ad hoc release and parses its weights
sheet, and the monthly workflow runs it before reconciling. But that parser was
written *without sight of the spreadsheet* — ons.gov.uk is blocked by egress
policy from the sandbox this was built in — so its layout assumptions are
inferred, not observed. It is written to fail loudly and dump the workbook
structure rather than guess (see [Weights](#weights)). Until it has run
successfully once against the real file, treat weights as unconfirmed: the
committed `weights.csv` is an equal-thirds placeholder, `load_weights()` refuses
to return placeholders to the validation path, and every reconstructed row
carries `weights_are_placeholder` so a placeholder-based aggregate cannot be
mistaken for a real one.

The pipeline is designed to make these visible rather than to paper over them.

---

## The methodology being replicated

From ONS FOI-2023-1164 ("Aggregate index of air fares methodology") and the ONS
CPI/RPI Technical Manual §9.5.5:

| Category | Collected before departure | Return leg |
|---|---|---|
| Domestic | 1 month | +1 week |
| European / short-haul | 1 month **and** 3 months | +2 weeks |
| Long-haul | 1, 3 **and** 6 months | +3 weeks |

- **Index day** is usually the 2nd or 3rd Tuesday of the month. ONS withhold it
  in advance (they consider publishing it ahead of time commercially sensitive)
  and confirm it retrospectively in the following month's CPI bulletin, under
  "methodology information".
- **Flights depart on index day**, not at a rolling day-offset from collection.
- **Return flights are included in the price.** ONS price a return trip.
- **The flight chosen is the one departing closest to a fixed target time**, held
  constant month to month — *not* the cheapest fare on the day.

### Three places this diverges from a naive reading of the spec

These are deliberate, and each one materially changes the numbers.

**Departure dates are index days, not `today + 30`.** A rolling 30/90/180-day
offset lands on a different weekday every month, and day-of-week is one of the
largest single drivers of fare level. Anchoring both collection and departure to
Tuesdays removes a large spurious wobble. The `days_out` column keeps the
conventional 30/90/180 labels for stable grouping; `days_out_actual` records the
true gap, which is 28–35 days for a "1 month" window and never exactly 30.

**Returns are priced, not one-ways.** The brief's Task 1 said one-way, but its
own methodology table lists return legs, and the ONS FOI is explicit that return
flights are included in the price. Priced as a return.

**Both selection rules are stored.** A cheapest-of-day rule silently migrates
between a 06:10 departure one month and a 21:45 the next, so much of the
resulting "price change" is just the time-of-day fare curve moving underneath
you. `price_gbp` applies the ONS rule; `price_cheapest_gbp` records the cheapest
for comparison, and `ons_rule_time_delta_minutes` records how close to the target
time we actually got.

### One thing that is genuinely unresolved

Whether a fare is attributed to the month it **departs** or the month it was
**collected** is not settled by any public ONS source. The brief asserts
departure-month; standard CPI practice would suggest collection-month. Rather
than guess and bake it in, both are stored on every row
(`index_month_departure`, `index_month_collection`) and both are computed at
reconciliation time (`attribution_rule`). Validation scores them side by side
and lets the data settle it. `index_month_hyp` is retained under its original
name and follows the departure-month rule.

The same applies to aggregation: mean, median and geometric mean are all
computed. ONS use a Jevons (geometric mean) elementary aggregate for most CPI
items, so the geometric mean is the most likely match, but all three are carried.

---

## Route panel

23 routes, 44 queries per run (domestic ×1 window, European ×2, long-haul ×3).
All London-origin, economy, one adult, priced as advertised.

- **Domestic (8):** LHR/LGW/STN → EDI, GLA, BFS, ABZ, JER
- **European (9):** LGW/LHR/STN → AGP, ALC, NAP, CDG, AMS, DUB, PMI, FAO
- **Long-haul (6):** LHR/LGW → JFK, DXB, MCO, CPT, SIN

Each route carries a `rationale` field so the sample's composition stays
auditable. Note the brief listed "London–Amalfi"; Amalfi has no airport, so the
panel uses **Naples (NAP)**, the Amalfi Coast gateway.

This is a proxy, not a reproduction — ONS's actual route sample is randomly
selected and not public. The goal is representativeness in the same dimensions
(haul type, leisure/business character, London airport mix), calibrated against
ONS's published sub-indices.

---

## Setup

### 1. BigQuery

```bash
export GCP_PROJECT=your-project
export BQ_DATASET=airfares
python -m ukairfares.ensure_tables
```

Creates `airfare_scrapes` (partitioned by `scrape_date`, clustered by
`haul_category, route`) and `reconstructed_index` (partitioned by
`index_month`). Both are **append-only** — see [Invariants](#invariants).

### 2. GitHub secrets

| Secret | Purpose |
|---|---|
| `GCP_PROJECT` | BigQuery project ID |
| `BQ_DATASET` | BigQuery dataset name |
| `GCP_SA_KEY` | Service-account JSON. Needs `bigquery.dataEditor` + `bigquery.jobUser` |
| `TRAVELPAYOUTS_TOKEN` | Travelpayouts API token |

Nothing is read from a committed file. The service account should be scoped to
this dataset only.

### 3. Verify without spending anything

```bash
pip install -r requirements-dev.txt
python -m pytest                                    # 153 tests, no network
DRY_RUN=1 FARE_PROVIDER=mock PYTHONPATH=src \
  python -m ukairfares.pull --scrape-date 2026-08-11 --dry-run-out /tmp/dry.ndjson
```

The mock provider generates deterministic synthetic fares, so the whole
pipeline — calendar, panel, selection, row construction — is exercisable with no
token and no quota.

---

## Running

```bash
# One collection run
PYTHONPATH=src python -m ukairfares.pull

# Reconstruct a month once ONS confirm the index day
PYTHONPATH=src python -m ukairfares.reconcile --index-month 2026-08

# Bypass the bulletin parser if you have read the index day yourself
PYTHONPATH=src python -m ukairfares.reconcile --index-month 2026-08 --index-day 2026-08-11

# Score against published ONS values
PYTHONPATH=src python -m ukairfares.validate
```

### Schedules

| Workflow | Cadence | Behaviour |
|---|---|---|
| `airfares-daily-pull` | `0 9 * * *` | Runs daily on the 8th–21st; Mondays only outside that window |
| `airfares-monthly-reconcile` | `0 12 15-25 * *` | Attempts daily; no-ops until the bulletin is out |
| `airfares-ci` | on push/PR | Tests + an end-to-end mock dry run |

The 8th–21st window is not arbitrary: it is exactly the range that brackets
every possible 2nd or 3rd Tuesday, in every month. (There is a test asserting
this holds across 2025–2027.) The gate is evaluated inside the job because
GitHub ORs cron's day-of-month and day-of-week fields, which makes
"8th–21st, otherwise Mondays" inexpressible in schedule syntax alone.

### Failure policy

Two requirements pull in opposite directions — "don't let one route kill the
run" and "fail loudly rather than silently skip". Resolved as:

| Condition | Behaviour |
|---|---|
| One route/window fails | Retry once with backoff, then write an `error` row and continue |
| Failure rate > `FAILURE_THRESHOLD` (default 34%) | Exit **1** — the vintage is not trustworthy |
| Zero queries attempted | Exit **1** |
| Bad config / missing token | Exit **2** immediately, before querying anything |
| Bulletin not published yet | Exit **0** with a notice — expected, not an error |
| Bulletin published but unparseable | Exit **1** — our parser broke, and silence would skip the month forever |

Failures are written to BigQuery as rows, not merely logged. An absent row and a
failed row are different facts, and only one of them is recoverable later.

---

## Reading the index day out of the bulletin

Parsing prose for a date is brittle, so `onsfetch` leans on a structural check
rather than on clever regex: **a valid index day is the 2nd or 3rd Tuesday of
the target month.** That single constraint rejects essentially every possible
mis-parse — publication dates, reference-period dates, next-release dates —
because none of them reliably land on one of exactly two days in the month. A
candidate failing it is discarded regardless of how confident the surrounding
wording looked, and if nothing parses the job exits non-zero rather than
guessing. A wrong index day would silently corrupt every reconstruction built on
it.

---

## Weights

The ONS sub-index weights (the domestic / European / long-haul shares of CPI
item 07.3.3) do two jobs: they combine the three haul series into the single
aggregate ONS actually publish, and once real, they give a second independent
thing to score against. They do *not* weight routes within a category — ONS's
within-category route weighting isn't published, which is why every `Route`
carries `weight = 1.0`.

```bash
# Fetch from the pinned release
PYTHONPATH=src python -m ukairfares.onsweights

# Search ONS for a newer ad hoc vintage first
PYTHONPATH=src python -m ukairfares.onsweights --discover

# Inspect the spreadsheet without parsing it — start here if parsing fails
PYTHONPATH=src python -m ukairfares.onsweights --dump
```

The parser locates the sheet, header row and columns by *searching* rather than
by fixed offsets, tolerates reordered columns and "short-haul"/"long haul"
naming variants, and validates every row (three positive weights, plausible
year, no duplicates). Anything it cannot read defensibly is rejected with a dump
of the workbook's actual structure — because a wrong weight silently corrupts
every aggregate built on it, and a loud failure costs one CI log while a quiet
mis-parse costs the whole series. If the layout differs from what it expects,
`_find_weight_sheet` / `_find_header` are the only two functions to change.

The weighted `haul_category = "all"` row is a weight-weighted mean of the three
haul **levels**. Note that the month-on-month change of a weighted level is not
the same as the weighted mean of the three changes — long-haul's much larger
absolute fares dominate the former regardless of its weight. The statistically
correct aggregate needs two months in hand, so it belongs in validation, not
reconciliation; the `"all"` row is a convenience level, not the headline.
Combinations missing any haul are skipped rather than partially weighted.

---

## Validation

`validate.py` is deliberately hard to get a favourable answer out of. Guards, in
the order they bite:

1. **Minimum overlap.** Under one full quarter of backfilled
   `published_ons_value` months → `INSUFFICIENT_DATA`, no headline MAE. With
   n=2 the number means nothing, and index-day-timing risk means early
   reconstructions may be off by a week or more of fare drift.
2. **Rolling origin.** Errors are reported as a rolling-origin sequence, each
   month scored on what was knowable before it — the same discipline as the RPI
   Rent workbook. An in-sample average across all months would flatter the
   pipeline by letting later months inform earlier ones.
3. **Variant-selection honesty.** Several attribution/selection/aggregation
   combinations are computed. Whichever scores best was chosen *after* seeing
   the answers, so its MAE is optimistically biased — the report says so
   explicitly and states how many variants were in the running.
4. **Provenance blockers.** Placeholder weights, a cache-backed source, or a
   substituted scrape date each downgrade the verdict regardless of the numbers.

Errors are in **percentage points of month-on-month change**, not levels: our
reconstruction is a mean fare in pounds and ONS publish an index on a Jan=100
basis. The levels are not comparable; the movements are, and movement is what a
nowcast is for.

Verdicts are `INSUFFICIENT_DATA` → `PROVISIONAL` → `SCORED`. Do not claim the
pipeline works before `SCORED`.

---

## Invariants

- **`airfare_scrapes` is never UPDATEd and never DELETEd from.** Every pull is a
  new vintage. If a price looks wrong the fix is another row, not an edit — the
  whole point is to be able to reconstruct what we believed on any past date,
  which is impossible if history is mutable.
- **`reconstructed_index` is likewise append-only.** A month legitimately gains
  rows over time: one when the index day is confirmed, another when
  `published_ons_value` is backfilled, another if a variant is rescored.
  `computed_ts` orders vintages; `is_current` marks the latest.
- **Load jobs, not streaming inserts.** Cheaper, and rows are immediately
  queryable and partition-prunable.
- **No PII, ever.** Route and date searches only. No logins, no loyalty numbers,
  no traveller data.

---

## Changing the fare source

The 2026 landscape is unstable, so this is built to be swapped. Implement
`FareProvider` (`providers/base.py`) — one `search()` method — and register it in
`providers/__init__.py`. Nothing else changes; all ONS-specific logic lives in
`onscal.py` and `selection.py`, not in providers.

State of the options as researched in August 2026:

| Source | Status |
|---|---|
| **Amadeus Self-Service** | **Decommissioned 17 July 2026.** Do not build against it. |
| **Kiwi.com Tequila** | Public self-serve closed May 2024; invitation-only partners since. |
| **Skyscanner** | Partner-only; approval unlikely without an established travel business. The "Sky Scrapper" RapidAPI listings are unofficial resellers — excluded as ToS-violating. |
| **Duffel** | Live NDC fares, self-serve, exact date/cabin/pax control — methodologically the cleanest fit. But its agreement enforces a 1,500:1 search-to-book ratio with *"zero Orders … treated as one Order"*, and reserves the right to cap usage to honour airline supplier agreements. A pure-research account that never books risks being capped or closed. Cost itself is trivial (~$3/month at this volume). |
| **SerpApi Google Flights** | Live advertised fares, closest to what ONS collectors actually do, ~$25/month at this volume. But it is scraping-as-a-service under active litigation (Google's DMCA claims dismissed July 2026; Reddit suit ongoing). |
| **Travelpayouts** ← *current* | Free, self-serve, but cache-backed (48h) rather than live. See [limitations](#read-this-first-what-this-pipeline-can-and-cannot-currently-tell-you). |

---

## Layout

```
uk-airfares/
├── sql/
│   ├── 001_airfare_scrapes.sql        Append-only observation panel
│   └── 002_reconstructed_index.sql    Monthly reconstructions
├── src/ukairfares/
│   ├── onscal.py       Index-day calendar arithmetic — the core of the thing
│   ├── panel.py        Route panel + ONS weight loading
│   ├── selection.py    ONS closest-to-target-time rule vs cheapest
│   ├── config.py       Environment-driven config
│   ├── bq.py           Append-only BigQuery writer + dry-run writer
│   ├── pull.py         Daily collection (Task 3)
│   ├── onsfetch.py     CPI bulletin index-day parser
│   ├── onsweights.py   Fetches + parses ONS sub-index weights
│   ├── reconcile.py    Monthly reconstruction (Task 4)
│   ├── validate.py     MAE/bias scoring (Task 6)
│   └── providers/      base.py · travelpayouts.py · mock.py
└── tests/              153 tests, no network required
```

## Non-goals

- Not scraping airline or OTA websites.
- Not replicating ONS's exact route/fare-class sample — it isn't public. The
  goal is a well-calibrated proxy validated against ONS's own published
  sub-indices.
- Not storing personal or traveller data of any kind.

## Sources

- [ONS FOI-2023-1164 — Aggregate index of air fares methodology](https://www.ons.gov.uk/aboutus/transparencyandgovernance/freedomofinformationfoi/aggregateindexofairfaresmethodology)
- [ONS FOI — Methodology used for the air fares index](https://www.ons.gov.uk/aboutus/transparencyandgovernance/freedomofinformationfoi/methodologyusedfortheairfaresindexandmobilephoneapplications)
- [ONS ad hoc — Domestic, European and long-haul airfares sub-indices, Jan 2017 to Feb 2025](https://www.ons.gov.uk/economy/inflationandpriceindices/adhocs/2716domesticeuropeanandlonghaulairfaresconsumerpricessubindicesjanuary2017tofebruary2025)
- [ONS CPI bulletin series](https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation)
- [Travelpayouts Aviasales Data API](https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API)

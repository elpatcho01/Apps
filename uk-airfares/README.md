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

**1. Only one month of collection exists, and no month is complete.** Collection
began 2026-08-17. The SerpApi adapter has run live successfully (44/44 rows) and
`raw_response` retains every payload so observations can be reprocessed without
re-querying — but **no accuracy claim is possible until a full quarter of
overlap with ONS's published series exists**, and `validate` returns
`INSUFFICIENT_DATA` until it does. That is enforced, not advisory. The
Travelpayouts adapter remains untested against its live API.

**2. Coverage on thin routes and distant dates is unmeasured.** A specific
Tuesday six months out on, say, LHR–CPT may return nothing. That is recorded as
a `no_data` row, not an error, so gaps are visible in the panel rather than
silently absent. Expect long-haul 180-day coverage to be the weakest cell.

**3. The committed `weights.csv` is still a placeholder.** Both ONS parsers have
now run successfully against the real workbook — the layouts documented below are
observed, not inferred — but the weights refresh happens in the *ephemeral*
Actions checkout and is not committed back. So a local run uses placeholders
unless you run `onsweights --discover` yourself first. The guard rails hold
regardless: `load_weights()` refuses to hand placeholders to the validation path,
and every reconstructed row carries `weights_are_placeholder`, so a
placeholder-based aggregate cannot be mistaken for a real one. Per-haul
reconstructions do not use weights at all and are unaffected.

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

**Candidates are filtered to comparable products before the rule is applied.**
The ONS target-time rule is price-blind by design — it takes whichever flight
departs nearest the target, whatever it costs. Day one of live collection showed
why that is only safe over a comparable candidate set:

```
LGW-EDI  £4,841  SWISS       09:25   (cheapest direct £72)
LHR-ABZ  £3,215  Air France  08:55   (cheapest direct £135)
```

SWISS does not fly Gatwick–Edinburgh; that is LGW–Zurich–EDI. Google Flights
lists such constructed routings alongside direct services, and the rule grabbed
them for departing near 09:00. Selection now takes **direct services only**
where any exist, with an outlier cap (default 5× the cheapest) behind it.
`candidate_basis` and `n_quotes_considered` record what was filtered, per row.

**The target time is per haul, because one constant did not work.** Four
consecutive collection days (2026-08-17 to 20) showed the long-haul cells moving
**4.5–7.2% day to day while the price-blind cheapest fare on the same queries
moved 0.6–2.1%**. The market was nearly still; the movement was ours.

`ons_rule_time_delta_minutes` shows the mechanism — it oscillated
`173 → 273 → 173 → 273` for long-haul while short-haul sat flat at 59 and 66.
Long-haul departures cluster in a bank set by arrival slots and night-flight
curfews (observed 114–273 minutes after 09:00, so roughly 11:00–13:30). Nothing
departs near 09:00, so every candidate was hours away and one flight entering or
leaving the provider's result set relocated the choice by ~100 minutes. Long-haul
1-month has a median published monthly move of 7.7%; the rule was generating
6.2% of noise on its own.

Long-haul now targets **12:00**, inside its actual bank. Short-haul is unchanged
at 09:00 — it was already stable and there was nothing to fix.

**That fixed the 1-month cell and did not fix the other two.** Re-running the
same test on 2026-09-12, over consecutive collection days either side of the
change:

```
                    ours d/d   cheapest   ratio
PRE   long_haul 1m      7.0%       2.3%     3.0
      long_haul 3m      6.2%       0.6%     9.9
      long_haul 6m      4.2%       2.3%     1.8
POST  long_haul 1m      4.1%       3.7%     1.1   <- fixed
      long_haul 3m     10.1%       0.9%    10.7   <- unchanged
      long_haul 6m     12.4%       0.4%    28.0   <- worse
```

**The reason is one route, and the haul-level framing was wrong.** Mean distance
from a 12:00 target, measured per route on 2026-09-11:

| Route | Off target | Departs |
|---|---|---|
| **LHR–CPT** | **467m** | 18:25, 22:30 |
| LHR–DXB | 115m | 13:40, 14:25 |
| LHR–SIN | 67m | 10:40, 11:20 |
| LGW–MCO | 60m | 10:30, 11:15 |
| LGW–JFK | 20m | 11:30, 11:45 |
| LHR–JFK | 15m | 11:55, 12:20 |

Exclude LHR–CPT and long-haul sits **52–62 minutes** off target — better than
domestic (63m) and close to European (40m). The 12:00 target is fine for five
routes of six; the entire miss is one sector.

And CPT cannot be fixed by moving the target. It is an ~11.5-hour overnight timed
to arrive in the morning, so it departs in the evening and there is no midday
service. No single clock time serves CPT (18:25), JFK (11:55) and DXB (14:25) at
once — they are different sector lengths into different time zones. With a 12:00
target CPT's two candidates sit 385 and 630 minutes away, so whichever appears in
the result set wins: different aircraft, different fare buckets, on the most
expensive route in the panel.

**The other half of the problem is not the target time at all.** LHR–SIN on
2026-09-11 priced £2,172 against a £748 cheapest while sitting 40 minutes from
target — direct, economy, the right flight by the rule. What differed was that
flight's own fare bucket: cheap inventory sells out per departure and closer to
travel, so a correctly-chosen flight can still carry a price driven by its own
booking curve rather than by the market.

Moving the target cannot fix that. The candidate methodology change is
**matched-model pricing**: CPI prices the same item month to month, substituting
only when it disappears, where our rule re-picks from scratch every month. That
would remove the flipping by construction — but it is a bet on how ONS operate,
which they have never published, and a rule that is stable *because* it ignores
substitution is not automatically a rule that is right.

So it is measured rather than adopted. `selected_flight_number` (sql/008) records
the identity that survives a date change — BA 059 on 8 September and BA 059 on
13 October are one item in CPI terms, which a departure timestamp cannot express
— and `python -m ukairfares.matched` replays both rules over payloads already
collected and reports which produces less month-to-month movement. It runs
monthly in the digest workflow and writes nothing.

It will answer **"not enough history"** until roughly February 2027, and that is
the correct answer: two index months give one step per series, and one step
cannot distinguish a quieter rule from a lucky one. The month it stops saying so
is the month the decision becomes possible.

**So the bank belongs to the sector, not the haul.** `target_departure_time_for`
now takes a route and consults `TARGET_DEPARTURE_TIME_BY_ROUTE` before falling
back to the haul. That table ships **empty on purpose** — deriving targets from
the flights we already selected would be circular, since the selected flight is
whatever was nearest the existing target. `python -m ukairfares.banks` reads the
real candidate distribution out of `raw_response` and prints a block to paste in,
refusing to propose a number for a route with fewer than 20 observations or a
timetable too diffuse to have a centre. Until it has run, every route falls back
to its haul and behaviour is unchanged.

*Sample-size caveat, because the numbers above are small enough to mislead:*
three and four consecutive-day pairs per cell. Only 2026-09-08 to 11 are truly
consecutive post-change; the rest of the window is the Monday baseline, and a
seven-day gap is not a day-over-day move. Treat this as "not yet solved" rather
than as a measured regression. It is recorded here so the earlier fix is not
read as more complete than it was.

A second mechanism is now visible and is **not** a target-time problem: on
2026-09-11 LHR–SIN selected a £2,172 fare against a £748 cheapest at 1 month and
£2,092 against £589 at 3 months, while the 6-month window sat at only +21%. Same
route, same airline, direct-only, 40 minutes from target — the rule picked the
right flight. What differs is that flight's own fare bucket: cheap inventory
sells out per departure and closer to travel, so a stably-chosen flight can still
carry a price driven by its own booking curve rather than by the market. The
digest now flags this per route (see `PRICE_OUTLIER_RATIO`); confirming it needs
`raw_response`, which retains every quote.

*The tension, stated because it is real:* ONS do not publish their target time.
If theirs is 09:00 everywhere, this is a divergence. But 09:00 was always a
guess, and a guess landing where no aircraft departs is not the faithful choice —
it just makes the selection arbitrary. Both escapes are open:
`TARGET_DEPARTURE_TIME` in the environment still forces one time across every
haul, `target_departure_time` is stored on every row so a mixed history is
unambiguous, and `raw_response` retains every quote so any target can be
re-derived over data already collected.

**`selection_margin_minutes` makes fragility visible per row.** How much further
from the target the runner-up sat. Small means the choice was a near-tie and one
flight appearing or vanishing would have changed it; NULL means there was no
runner-up at all. The flip above was only detectable by comparing days in
aggregate — nothing on an individual row said "this was a coin flip". Now it does.

*It did not, for a month.* The column was computed, written and migrated, and
then selected by neither the digest nor the export — so the diagnostic built to
make fragility visible was collected on every row and read by nobody. Fixed, and
both consumers now carry it. Note the export also counts `sole_candidate`
separately, because `AVG` skips NULLs: without that count, a series where most
rows had no runner-up at all would average the few that did and read as a
confident wide margin, which is the opposite of the truth.

**Both selection rules are stored.** A cheapest-of-day rule silently migrates
between a 06:10 departure one month and a 21:45 the next, so much of the
resulting "price change" is just the time-of-day fare curve moving underneath
you. `price_gbp` applies the ONS rule; `price_cheapest_gbp` records the cheapest
for comparison, and `ons_rule_time_delta_minutes` records how close to the target
time we actually got.

### ONS publish six series, not three

Confirmed from the real workbook (a production run dumped it). Each haul
category is broken out by **advance window**:

| Series | Windows published |
|---|---|
| Domestic | 1-month |
| European | 1-month, 3-month |
| Long-haul | 1-month, 3-month, 6-month |

Six published series in total. Our panel already collects at exactly that
granularity (`months_ahead`), so reconstructions are produced per
(haul × window) to be directly comparable — collapsing the windows together
would compare against something ONS never publish.

The workbook itself is one sheet per year, transposed (months across columns,
series down rows), with the category label merged across its window rows.

**The release covers January 2007 to February 2026** — its own title says so —
but what actually loaded was **2016-01 onward: 678 values, 113 months, six
series**. That was a parser bug, not a limit of the release. A year floor of
2015, written when the pinned release was titled "January 2017 to February 2025",
silently discarded every earlier sheet: `_as_year` returned `None`, and the loop
skipped it without a word. Nothing errored; the history simply was never there.

Fixed. **Re-run the backfill workflow to pick up the missing years**, which should
roughly double the answer key. The general lesson is worth keeping: a bound
derived from one release's title becomes a silent filter the moment the release
changes.

**The answer key is refreshed once a year, in March.** From the July 2026
bulletin:

> We updated our Domestic, European and long-haul air fares consumer prices
> subindices and weights dataset on **25 March 2026** with data for March 2025 to
> February 2026, and a longer historical time series back to 2007.

That explains why the series stops at 2026-02, and it sets the timetable: the
next vintage lands around **March 2027**. Since collection began 2026-08, the
overlap is currently **zero months** and stays zero until then, so no accuracy
claim is possible before it. The same bulletin notes that "from March 2026, we
have also started to release quarterly and annual average air fares" — worth
chasing, because a quarterly release would shorten that wait considerably.

The monthly backfill workflow re-runs discovery and picks up a newer vintage
automatically, and the digest reports the remaining gap every month, so this is
tracked rather than assumed. Confirm what is currently loaded with the coverage
query in [Backfilling](#backfilling-onss-published-series).

**Nine months are absent, identically across all six series:** 2020-04, 2020-05,
2020-06, 2020-11, and 2021-02 through 2021-06. That is 113 months out of a
122-month span. The dates are the UK lockdown windows — with almost no flights to
price, ONS suspended air-fare collection and imputed the CPI item rather than
publishing a collected index. Two consequences:

- **Rolling-origin validation must skip them.** They are not zeroes or dips to be
  explained; there is no observation. A month-on-month relative spanning a hole
  is meaningless.
- **The "one full quarter of overlap" gate has to land on months that exist.**
  Three consecutive *published* months, not three consecutive calendar months.

**The six series do not peak in the same month.** Taking the median across all
years, domestic and both European windows peak in **August** (European 1-month
reaches 219), while long-haul 1-month and 6-month peak in **December** (162 and
149). Summer holidays versus Christmas travel. Any seasonal adjustment or
sanity check applied uniformly across hauls will therefore be wrong for half the
series.

**Weights are per series too.** Six weights per year, one per (haul × window),
summing to 1. The split across a category's windows is *not* even — long-haul
1-month carries ~0.056 while its 3- and 6-month windows carry ~0.251 each — so
it has to be read from the file, never derived.

**Basis confirmed:** every January is exactly 100 and each year restarts from
there — `annual_january_100`, not chain-linked. That resolves the ambiguity the
`detect_basis` helper was written to settle.

The published data is far more volatile than you might expect: European
1-month ran 100 → 224.92 within 2019, and long-haul 1-month dipped to 74.44.
Worth internalising before judging any nowcast's error.

### One thing that is genuinely unresolved

Whether a fare is attributed to the month it **departs** or the month it was
**collected** is not settled by any public ONS source. The brief asserts
departure-month; standard CPI practice would suggest collection-month. Rather
than guess and bake it in, both are stored on every row
(`index_month_departure`, `index_month_collection`) and both are computed at
reconciliation time (`attribution_rule`). Validation scores them side by side
and lets the data settle it. `index_month_hyp` is retained under its original
name and follows the departure-month rule.

**The published seasonality points hard at departure-month.** Not proof, but the
strongest evidence available before overlap exists, and it costs nothing to
check: take the peak month of each published series and ask what each reading
implies about when people are flying.

| Series | Peaks | If index = departure | If index = collection |
|---|---|---|---|
| Domestic 1m | Aug | depart **Aug** | depart Sep |
| European 1m | Aug | depart **Aug** | depart Sep |
| European 3m | Aug | depart **Aug** | depart Nov |
| Long-haul 1m | Dec | depart **Dec** | depart Jan |
| Long-haul 3m | Dec | depart **Dec** | depart Mar |
| Long-haul 6m | Aug | depart **Aug** | depart Feb |

Under departure-month every series peaks for August or December travel — summer
holidays and Christmas. Under collection-month, long-haul fares would have to
peak for **January and February departures**, the two cheapest months of the year
to fly long-haul. The second reading is not credible.

The same test explains the 08→09 transition, which is the most reliably negative
step in the calendar (negative in 19 of 19 years, median −22% to −27%). Under
departure-month that is August travel giving way to September travel, which is
the post-summer collapse. Under collection-month it would be November departures
giving way to December ones — Christmas — which should *rise*.

*The counter-argument, stated because it is real:* standard CPI measures prices
collected in the reference month, and departure-month attribution means a
September 6-month figure was collected the previous March. That is unusual
timing. Air fares are already one of the few items collected forward-looking, so
it is not disqualifying, but ONS have published nothing either way. Both
attributions remain stored and scored.

The same applies to aggregation: mean, median and geometric mean are all
computed. ONS use a Jevons (geometric mean) elementary aggregate for most CPI
items, so the geometric mean is the most likely match, but all three are carried.

### Read the panel at index-month grain, or it will mislead you

This is the single most important habit when looking at this data, and getting it
wrong produced a published chart that was flatly wrong for a day.

**Departure dates roll forward with the collection date.** An August collection at
3 months ahead prices a 10 Nov departure returning 1 Dec; the September one prices
8 Dec returning **29 Dec**. Charted against collection date, consecutive points
are different trips in different months, and the series appears to rise 28% in a
month that has fallen in 19 years out of 19. Nothing was wrong with the fares —
the panel had walked out of an off-peak November trip into a Christmas one, which
ONS's own history says adds about 38% to that series.

So: **group by `index_month_departure`, never by `scrape_date`**, whenever the
question is about fare movement. `routes_by_index_month` in the export exists for
exactly this, at the route grain.

### Two live measurement problems, both found this way

Placing the first two index months on the ONS calendar and checking each step
against the same calendar step since 2007 isolates two cells. One has a known
cause; one does not.

**Long-haul 1-month is not measuring fares.** Its Sep→Oct step was **+30.5%**,
which is 4.4 standard deviations above the historical mean and more than double
the widest such move in 19 years. The price-blind control on identical queries
moved **+3.7%**, entirely ordinary. The market did not do this; the selection did
— `ons_rule_time_delta_minutes` shows the rule settling onto a different, dearer
flight (173→273 oscillation in August, then locked at 153–157). Treat this cell
as unusable until the long-haul target time is measured per window rather than
inherited.

**European 1-month is unexplained and is not a selection problem.** There the
*rule* looks normal (−1.7%, 63rd percentile) and the **cheapest comparable fare**
is the outlier: **+18%**, above its own 19-year maximum of +13%. A price-blind
control cannot be moved by the selection rule, so this is something else. Ruled
out so far:

- *Not composition.* All 9 European routes priced on all 12 collection days, zero
  `no_data`, so it is not the unmatched-sample artifact described under
  [Matched samples](#matched-samples).
- *Not one route.* The mean/median ratio holds at 0.95–1.08 throughout with no
  shift between months.
- *Not noise.* The cheapest sits at £82–85 across all seven August days and
  £95–103 across all five September days — a clean level shift.

The half-term hypothesis was tested and is **mostly wrong**. European returns are
+2 weeks, so the October trip departs 13 Oct and returns 27 Oct, inside UK autumn
half-term, while the September trip is term-time on both legs. That is real, but
half-term recurs every year and is therefore already in ONS's baseline — the only
thing that varies is which side of it the return falls, and that is worth far
less than the anomaly. Measured across 19 years, splitting on whether the October
return lands in the half-term week:

```
return IN half-term      n= 8   median Sep->Oct  -2.8%
return NOT in half-term  n=11   median Sep->Oct  -7.4%
```

So the alignment is worth about **4.6 percentage points**. The anomaly is ~21
points above the −3.3% median. Half-term accounts for at most a quarter of it,
and no year in 19 — including every favourable alignment — reached +18%.

What remains is **unexplained**. One amplifier worth testing: our nine European
routes are leisure-weighted (AGP, ALC, FAO, PMI, NAP are beach destinations),
where ONS's sample is broader and unpublished, so our basket should be more
half-term sensitive than theirs. Whether that closes a 4.6-point effect into a
21-point one is exactly what the per-route split answers.

**The test that settles it:** if half-term is the cause, the rise concentrates in
the leisure routes (AGP, ALC, FAO, PMI, NAP) and is weak on the business routes
(AMS, CDG, DUB). Spread evenly across all nine, half-term is not the explanation.
`routes_by_index_month` makes this answerable from the committed export.

### The half of the trip nobody is measuring

Every quote prices a return trip, and the ONS target-time rule is applied to the
outbound and to nothing else. The return is whatever the provider bundled — in
practice the cheapest available return for the chosen outbound — so half of each
priced trip is selected by exactly the rule this project argues against for the
other half: *a cheapest-of-day rule silently migrates between a 06:10 departure
one month and a 21:45 the next, so much of the resulting price change is just the
time-of-day fare curve moving underneath you.*

It is worse than uncontrolled, because it is also unrecorded: `return_at` on a
quote is a placeholder — midnight of the date requested — not a departure anyone
observed. So the size of the problem has never been known.

That matters for the European anomaly above, because the two trips differ in a
way that fits its shape. September departs 8 Sep and returns 22 Sep, both
term-time; October departs 13 Oct and returns 27 Oct, inside half-term. If cheap
returns dry up in half-term, the cheapest *total* rises sharply while the rule's
already-dearer combination moves much less — +18% against −1.7% is that shape.

`returnleg.py` asks the free question first. Controlling the return means a
second `departure_token` call per query: +719 searches/month, a doubling of
spend. Before buying that, the module runs a **census** over the payloads already
in BigQuery — every key path, counted, with the share of rows each appears in —
rather than a parser for a shape nobody has confirmed. That is deliberate: a
parser that finds nothing cannot distinguish "the field is absent" from "I looked
in the wrong place", and it is how the sibling accommodation project settled the
same kind of question, where a raw-key census over 214 live properties found
`free_cancellation` in the key set of *none* of them and stopped a control being
designed against a field that was never there.

The census returns one of two answers, and both are actionable:

- **return detail is present** → the variation is measurable from data already
  paid for, and a median that moves between the September and October index
  months is the anomaly's explanation;
- **return detail is absent** → control requires the second call, and the cost is
  a real number rather than a hypothetical.

It also counts `departure_token`, because that decides the price of the fix: with
the token retained, the second call runs against stored payloads at one extra
search per query; without it, the outbound must be re-queried first and the fix
costs two. Nothing is written — it runs in the monthly digest beside the bank and
matched-model measurements, and prints.

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
export BQ_LOCATION=europe-west2   # London. Immutable once the dataset exists.
python -m ukairfares.ensure_tables
```

Creates the dataset if absent, then `airfare_scrapes` (partitioned by
`scrape_date`, clustered by `haul_category, route`), `reconstructed_index` and
`ons_published_index`. All **append-only** — see [Invariants](#invariants).
Idempotent, so the workflows run it before every job.

You need a GCP project with **billing enabled** (BigQuery requires a billing
account even to use the free tier) and the BigQuery API switched on. Usage here
is ~20 MB/year against a 10 GB free allowance, so expect a £0 bill.

### 2. GitHub secrets

| Secret | Purpose |
|---|---|
| `GCP_PROJECT` | BigQuery project ID |
| `BQ_DATASET` | BigQuery dataset name |
| `GCP_WIF_PROVIDER` | Workload Identity provider resource name (keyless auth — see below) |
| `GCP_SA_EMAIL` | Service-account email. Needs `bigquery.dataEditor` + `bigquery.jobUser` |
| `SERPAPI_KEY` | SerpApi API key (default provider) |
| `TRAVELPAYOUTS_TOKEN` | *Optional.* Only if `FARE_PROVIDER=travelpayouts` |

Nothing is read from a committed file, and there is **no service-account key**.
Authentication uses Workload Identity Federation: GitHub mints a short-lived
OIDC token per run and exchanges it for GCP credentials, so no long-lived
secret exists to leak or rotate. This is also required in practice — Google
applies `constraints/iam.disableServiceAccountKeyCreation` by default to new
projects, which blocks JSON key creation outright.

One-time setup (substitute your project and repo):

```bash
PROJECT_ID=your-project
REPO=owner/repo
SA=airfares-pipeline@$PROJECT_ID.iam.gserviceaccount.com
NUM=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud services enable iamcredentials.googleapis.com sts.googleapis.com

gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'"

# Let only this repo impersonate the service account.
gcloud iam service-accounts add-iam-policy-binding $SA \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"

echo "GCP_WIF_PROVIDER = projects/$NUM/locations/global/workloadIdentityPools/github/providers/github-provider"
```

The `attribute-condition` is what stops any other repository on GitHub from
exchanging a token for your credentials — do not omit it.

### 3. Verify without spending anything

```bash
pip install -r requirements-dev.txt
python -m pytest                                    # 443 tests, no network
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

# Write the monthly digest to reports/YYYY-MM.md
PYTHONPATH=src python -m ukairfares.digest --month 2026-08

# Export analytics JSON to reports/data/analytics.json
PYTHONPATH=src python -m ukairfares.export
```

### Schedules

| Workflow | Cadence | Behaviour |
|---|---|---|
| `airfares-daily-pull` | `0 9 * * *` | Runs daily on the 8th–21st; Mondays only outside that window |
| `airfares-monthly-reconcile` | `0 12 15-25 * *` | Attempts daily; no-ops until the bulletin is out |
| `airfares-backfill-ons` | `0 6 3 * *` | Refreshes the published ONS series and weights |
| `airfares-monthly-digest` | `0 7 2 * *` | Writes and **commits** `reports/YYYY-MM.md` |
| `airfares-ci` | on push/PR **and weekly (Sun 07:00)** | Tests, workflow lint, dependency canary |

### Keeping it alive for months, not weeks

Four things exist purely so an unattended pipeline fails on a Sunday rather than
on an index day. Fares missed on an index day cannot be recollected, which is
what makes "find out in production" the expensive outcome.

**`requirements.txt` is a lock, not a range.** Exact pins including transitive
dependencies, taken from a verified production resolve. Previously it said
`requests>=2.31,<3` and resolved fresh every run, so an upstream minor release
could break collection with no change on our side.

**A weekly `latest-deps` canary** installs *unpinned* and runs the suite, and is
allowed to fail. That is the answer to the obvious objection to pinning — that
you never learn an upgrade breaks you. A red canary is information, not an
incident: production runs the lock and is unaffected.

**`actionlint` runs in CI** over `airfares-*.yml`. On 2026-08-18 a commit from
the sibling `uk-hotels` project left an invalid GitHub expression in
`airfares-backfill-ons.yml`. CI passed, because all it did was run pytest; the
only symptom was a workflow run named after the *file path* instead of the
workflow — GitHub's quiet signal for "this does not parse" — and the backfill was
dead for ~50 minutes. Verified: actionlint flags that exact commit and exits 1.
Scoped to our own files, since a neighbour's mistakes turning this suite red is
how a suite gets ignored.

**Every job has `timeout-minutes`.** They did not, so each defaulted to GitHub's
six hours — and with `cancel-in-progress: false` a hung run holds the concurrency
slot, queues one replacement and cancels the rest. One stuck run could have cost
several days of collection.

**Dependabot** (`.github/dependabot.yml`, monthly) maintains both the pins and
the action versions. The action bumps matter: every run currently warns that
`checkout@v4`, `setup-python@v5`, `upload-artifact@v4` and `auth@v2` target Node
20 and are being forced onto Node 24. When that shim goes, all five workflows
break at once. Delegated rather than hand-edited because guessing a tag that does
not exist breaks everything immediately — a worse failure than the one being
fixed.

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
| Index month predates the panel | Exit **0** with a notice — permanent absence, nothing to fix |
| Bulletin unparseable, month predates the panel | Exit **0** with a notice naming the parse breakage |
| Index day missing *during* active collection | Exit **1** — the puller broke |

Whether a month predates the panel is established **before** the bulletin is
fetched, because it is knowable without it — the index day is always the 2nd or
3rd Tuesday. On 2026-08-19 it was not, and the run went red over a July bulletin
that would not parse, for a July index day that could never have been used since
collection began 2026-08-17. The parse breakage is real and still reported; it
just no longer fails a run it cannot affect. It becomes fatal the moment
collection covers the month being reconciled.

To see what the parser sees, run the reconcile workflow with `dump_bulletin`:
ons.gov.uk is unreachable from the development sandbox by egress policy, so
Actions is the only place that can look at the page.

The last two look identical from inside a failing reconcile (no rows near the
index day) and are opposite in meaning, so `reconcile` checks `MIN(scrape_date)`
before deciding which it is. That check exists because the first fortnight would
otherwise be a wall of red runs: reconcile attempts last month daily from the
15th–25th, and until collection has a full month behind it that month is always
older than the panel. Six red runs for an absence you cannot fix is how someone
learns to ignore Actions email — and the 60-day trap below makes that expensive.

Failures are written to BigQuery as rows, not merely logged. An absent row and a
failed row are different facts, and only one of them is recoverable later.

---

## The monthly digest, and the 60-day trap it exists to defuse

`airfares-monthly-digest` writes `reports/YYYY-MM.md` — a summary of what was
collected last month, how healthy it looked, what was reconstructed, and what
needs attention. Read that one file and you have caught up.

It also solves a problem that would otherwise kill this pipeline quietly, about
two months after you stopped watching it:

> **GitHub disables scheduled workflows after 60 days of repository inactivity —
> and workflow runs do not count as activity. Only commits do.**

So the failure mode is specifically the success case. A pipeline that collects
perfectly every day for two months, needing no attention and therefore receiving
no commits, gets switched off on day 60. You get one email, easy to miss among
Actions notifications, and after that there is simply no data. Nothing errors;
the runs stop appearing. And the gap is unrecoverable, because you cannot go back
and collect August's index-day fares in October.

Committing the digest is a real commit on a monthly cadence, so the counter never
gets past ~30 days. The report that tells you the pipeline is healthy is the same
thing keeping it alive.

### Getting the data out of BigQuery

`reports/data/analytics.json`, written by the same workflow, is the export for
anything that wants to read the numbers without cloud access — a notebook, a
dashboard, an assistant.

It exists because **BigQuery cannot be queried from outside a workflow run.**
Service-account JSON keys are blocked by the
`iam.disableServiceAccountKeyCreation` org policy — Google's secure default, and
not worth weakening for convenience — and the Workload Identity Federation path
that replaced them mints a short-lived token from GitHub's OIDC provider, which
only exists inside a running job. The network reaches
`bigquery.googleapis.com` fine; the credential is the wall, and it is deliberate.
So the data leaves the same way it arrived: through a workflow.

What it contains:

| Section | Contents |
|---|---|
| `coverage` | Row/day/month counts and date ranges for both panel and published series |
| `published_series` | Every current ONS published value — the validation target |
| `daily_by_series` | One row per (day × haul × window): counts, mean/median/geomean fare, minutes off target |
| `latest_routes` | Per-route detail for the most recent collection date only |
| `routes_by_index_month` | One row per (index month × route × window): the grain ONS file at, so route-level movement between months is answerable without BigQuery. ~44 rows a month, not 44 a day |
| `reconstructions` | Every current reconstruction, all variants |

**Aggregates only, never raw observation rows.** Git keeps every version of
everything, so an export that grew with the panel — which gains ~44 rows a day
forever, each carrying a `raw_response` blob — would make every future clone pay
for it. And an export that could be mistaken for the panel would eventually be
treated as the panel; `airfare_scrapes` stays the single source of truth, with
`schema_version` and `generated_ts` on the export so a stale copy is recognisable.

Panel sections read `current_scrapes`, so a number here and the same number in
the digest come from the same rows by construction. Output is `sort_keys`'d, so a
month with no new data produces a byte-identical file and therefore no commit —
key ordering must not manufacture a diff.

For fresher data than monthly, dispatch the digest workflow by hand; it exports
and commits on every run.

### Why this workflow's failure posture is inverted

Everything else here fails loudly and early. This one does the opposite, on
purpose: **it must always reach its commit step.**

- `ukairfares.digest` wraps each query individually, so a failure becomes a note
  in the report rather than an exception. "Reconstructions unavailable:
  NotFound" is a useful digest. A digest that failed to generate is not.
- If generation fails outright anyway, the workflow commits a placeholder saying
  so, with a link to the run.
- Only after committing does it exit non-zero, so the run still shows red.

An aborting digest workflow would look like a minor annoyance and would take the
collection schedules down with it six weeks later. That is not a trade worth
making for a tidier exit code.

### The commit step once skipped itself on every scheduled run

Worth reading before touching the `if:` on that step, because the failure was
invisible for six weeks and the mechanism is not obvious.

The step used to be gated on `if: always() && inputs.commit != false`. The
`inputs` context is populated for `workflow_dispatch`, so on a `schedule` event
`inputs.commit` is null — and **GitHub coerces both null and false to `0` before
comparing**, which makes `null != false` evaluate to *false*. The step was
therefore skipped on exactly the runs that the mechanism exists for, and only
ever ran under manual dispatch.

Nothing failed. The digest was generated, the analytics JSON was exported, both
were discarded when the runner was torn down, and the run went green with the
step marked "skipped" — which reads like a condition doing its job. It was found
on 2026-09-12 by noticing that `reports/data/analytics.json` still described a
panel ending 2026-08-20 while collection had run cleanly every day since.

Two things follow, and both are now enforced by tests:

- **The gate lives in bash**, not in a GitHub expression — the same conclusion
  reached earlier about `${{ cond && '' || '--flag' }}`, arrived at a second time
  through a different operator. An empty string in bash is just an empty string.
- **`actionlint` does not catch this.** The expression is valid and lints clean;
  it simply means something other than what it appears to say. The suite now
  forbids comparing any context against a bare `true`/`false` literal.

The same line was present on both of the sibling `uk-hotels` commit steps, so
neither project had reset the inactivity clock since 2026-08-21.

### And once, five ALTERs in one file took the whole run with them

`sql/009` added five columns to `reconstructed_index` as five `ALTER TABLE`
statements. **BigQuery allows five table metadata update operations per table per
ten seconds**, the statements ran back to back, and the fourth returned:

```
Exceeded rate limits: too many table update operations for this table
```

`ensure_tables` exited non-zero, and because that step had no
`continue-on-error`, GitHub skipped every step after it — the digest, the
analytics export, and all three measurements. The run cost a runner and produced
nothing. The commit step still ran (it is gated on `always()`), so the 60-day
clock was never at risk; that part worked exactly as designed.

Two independent faults, fixed separately because either alone would recur:

- **The migration was over the limit.** One `ALTER TABLE` with several
  `ADD COLUMN` clauses is *one* metadata operation, so a migration adding any
  number of columns can sit well inside the budget. 009 is now a single
  statement, a test caps any file at four `ALTER`s against one table, and
  `ensure_tables` waits 12s and retries when the limit is met anyway — safe
  because every statement in `sql/` is `IF NOT EXISTS` or `CREATE OR REPLACE`.
- **The digest let a step skip the report.** Its stated posture is that nothing
  may prevent it reaching the commit; the DDL step quietly did not honour it.
  It is now `continue-on-error` with a warning, and a test asserts that *no*
  step between auth and the commit can skip the ones after it.

The posture is deliberately inverted in the daily pull, where `ensure_tables`
stays fatal and a second test pins that: the digest only reads, so it can run
against whatever schema is already there, but the pull **writes**, and writing
fares into a table missing a column loses them silently. Had this migration
reached `main` before it was caught, the next collection day would have failed
that way — and a missed index day cannot be recollected.

### Things worth knowing

- **Re-runs do not help.** Regenerating an identical report stages nothing, so
  there is no commit and no clock reset. The reset comes from each month's first
  run.
- **CI ignores `reports/**`** (via a `!` exclusion in `paths` — GitHub rejects
  `paths` and `paths-ignore` on the same event). Digest commits change no code,
  and a red CI run on a generated report would misrepresent the pipeline's state.
- **If the schedules do get disabled**, re-enabling them is a button in the
  Actions tab; the data gap while they were off cannot be backfilled.
- Any commit to the repository resets the clock — the digest just guarantees one
  arrives without you having to remember.

---

## Reading the index day out of the bulletin

**Confirmed working against the real July 2026 bulletin.** The wording ONS use is:

> The traditionally sourced data used in this release were collected on or
> around **14 July 2026**.

That is the 2nd Tuesday. Two things about it broke the first parser: it says
*data* collected, not *prices* collected, and **"on or around"** sits between the
preposition and the date.

The same bulletin contains a trap, and it is why the pattern now requires `on` to
follow `collected` immediately rather than across a wildcard:

> All domestic and European flight prices were collected after the outbreak of
> the conflict in the Middle East on **28 February 2026**.

That sentence carries "prices ... collected" and a date, and the loose pattern
preferred it. The Tuesday constraint rejected it — but only by luck of the
calendar. A wrong date landing on a 2nd or 3rd Tuesday would have been accepted
in silence.

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

## Making it like-for-like with ONS

Our reconstruction produces a **mean fare in pounds** (~£350). ONS publish an
**index number** on a January = 100 basis. These are not comparable in level and
never will be — we are not sampling the same routes, carriers or fare classes,
and ONS's sample isn't public. Any attempt to match levels would be measuring
our sample composition, not the fare market.

So don't. Contribute only the *change*, and take the level from ONS:

```
nowcast_level(m) = ONS published_level(m-1) × our price_relative(m-1 → m)
```

This is a **splice**. It's like-for-like in the only sense that matters — both
sides are a month-on-month price relative for the same CPI item — and the output
is a level on ONS's own basis, directly comparable to what they will publish,
without ever reproducing their history. It's also the number you'd actually act
on. `validate.py` reports its error as `splice_mae_index_points`.

### Matched samples

**This was built, tested, documented as load-bearing — and not wired in until
2026-09-12.** `index.py` had always implemented `matched_pairs` and
`price_relative`; nothing called it. `reconcile` stored an unmatched monthly
*level* and `validate` differenced those levels, so the protection below was
described accurately and applied nowhere. It is now in the path:
`reconstructed_index` carries `price_relative` alongside the level, and scoring
prefers it, falling back to differencing only where no relative could be
defended. The lesson generalises — a guard with no caller is documentation, not
a guard, and the only thing that distinguishes them is a test that fails when
it is removed.

The price relative is computed **only over routes priced in both months**. This
isn't fussiness. If `LHR-CPT` returns £900 in March and nothing in April, an
unmatched average reads the drop as a fall in prices when nothing about the fare
market changed. With a demand-driven cache producing routine `no_data` gaps,
unmatched aggregation would manufacture large phantom movements every month —
there's a test (`test_dropped_expensive_route_does_not_read_as_a_price_fall`)
showing the naive version inventing a 25% price collapse out of one missing
route. Matching is also what CPI does: price relatives are computed on matched
models.

A relative is refused below `min_matched` routes (default 3), and
`build_chained_index` **breaks the series** rather than carrying a level forward
when a month can't be chained — a fabricated level that looks like real data is
worse than a visible gap.

### Elementary aggregate formula

Which formula ONS use for item 07.3.3 specifically isn't established by any
public source we could reach. Jevons is used for most CPI items so it's the most
likely, but all three standard formulas are computed and tagged, and validation
settles it against published values:

| Formula | Definition | Note |
|---|---|---|
| **Jevons** | geometric mean of relatives | CPI default for most items |
| **Dutot** | ratio of arithmetic means | dominated by expensive routes |
| **Carli** | arithmetic mean of relatives | known upward bias; what a naive implementation does |

### Basis: detected, not assumed

The ad hoc release presents these sub-indices "on the January (of each year) =
100 basis", but whether the published series **resets** every January or is
**chain-linked** into a continuous one isn't clear from that description. Rather
than guess, `index.detect_basis` reads it off the backfilled data: if every
January is exactly 100, it resets. The answer is recorded on every row of
`ons_published_index`, and `rebase_to_january` can express our own series the
same way.

Note that our panel starts mid-2026, so we won't *have* a January to base on
until 2027 — another reason the splice, which needs no base month at all, is the
right primary construction.

---

## Backfilling ONS's published series

```bash
PYTHONPATH=src python -m ukairfares.backfill --discover
```

Loads ONS's actual sub-indices (January 2017 onward) into `ons_published_index`
— the validation answer key. It comes from the same workbook as the weights, so
one fetch serves both. Runs monthly via `airfares-backfill-ons`.

**What this does not do:** it does not let you reconstruct history. The fares
needed for that are unobservable in retrospect — an advertised fare is a quote,
not a record, and no provider retains them (Travelpayouts keeps 48 hours, Duffel
prices live inventory only, SerpApi scrapes live). Historical *shopping* data is
purchasable — OAG, who acquired Infare in 2023, hold roughly four trillion
historical airfares — and the pipeline is already replay-capable (the calendar
is pure date arithmetic; `pull.py --scrape-date 2019-06-11` works today). The
one interface change needed would be an *as-of* parameter on
`FareProvider.search()`.

What the backfill **does** buy you: the target series in BigQuery, so you can
size each haul category's real volatility before trusting any nowcast of it, and
so the comparison is already in place the moment live reconstructions land.

Worth being clear that backfilled *reconstructions* would have no standalone
value anyway — ONS already published every historical month. This pipeline's
entire value is the ~1-month lead on the current one.

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

- **Query `current_scrapes`, audit `airfare_scrapes`.** A date can carry several
  runs (a retry, a re-run, a double-click). The `current_scrapes` view exposes
  the latest run per date — one coherent vintage — and matches what
  reconciliation uses. Analysis queries should use it; a query against the raw
  table averages across every run for that date, which on 2026-08-17 produced a
  £870 "domestic fare" by pooling two buggy runs with one clean one.
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
| **SerpApi Google Flights** ← *current* | Live advertised fares, closest to what ONS collectors actually do, ~$25/month at this volume. Returns the full timetable with departure times, which the ONS target-time selection rule needs. Caveat: scraping-as-a-service under active litigation (Google's DMCA claims dismissed July 2026; Reddit suit ongoing). |
| **Travelpayouts** | Free, self-serve, but cache-backed (48h) rather than live. See [limitations](#read-this-first-what-this-pipeline-can-and-cannot-currently-tell-you). |

---

## Layout

```
uk-airfares/
├── sql/
│   ├── 001_airfare_scrapes.sql        Append-only observation panel
│   ├── 002_reconstructed_index.sql    Monthly reconstructions
│   ├── 003_ons_published_index.sql    ONS's own values — the answer key
│   ├── 004_add_months_ahead.sql       Migration: six series, not three
│   ├── 005_add_candidate_filter.sql   Migration: selection-pool diagnostics
│   └── 006_current_scrapes_view.sql   Latest coherent vintage per date
├── reports/                           Monthly digests (committed by Actions)
│   └── data/analytics.json            Analytics export (committed by Actions)
├── src/ukairfares/
│   ├── onscal.py       Index-day calendar arithmetic — the core of the thing
│   ├── panel.py        Route panel + ONS weight loading
│   ├── selection.py    ONS closest-to-target-time rule vs cheapest
│   ├── config.py       Environment-driven config
│   ├── bq.py           Append-only BigQuery writer + dry-run writer
│   ├── pull.py         Daily collection (Task 3)
│   ├── onsfetch.py     CPI bulletin index-day parser
│   ├── onsweights.py   Fetches + parses ONS weights and sub-indices
│   ├── banks.py        Measures each route's departure bank from raw_response
│   ├── matched.py      Replays matched-model vs re-picking, to decide on evidence
│   ├── returnleg.py    Censuses raw_response for the return leg nobody controls
│   ├── backfill.py     Loads ONS published series (the answer key)
│   ├── index.py        Matched-sample relatives, splicing, rebasing
│   ├── reconcile.py    Monthly reconstruction (Task 4)
│   ├── validate.py     MAE/bias scoring (Task 6)
│   ├── digest.py       Monthly report — also what keeps the schedules alive
│   ├── export.py       Analytics JSON — how data leaves BigQuery
│   └── providers/      base.py · serpapi.py · travelpayouts.py · mock.py
└── tests/              443 tests, no network required
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

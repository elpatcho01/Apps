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

**Coverage actually loaded: 2016-01 to 2026-02** — 678 values, 113 months, six
series. That is what the live backfill put in `ons_published_index`, read back by
the first digest run. It is wider than the Jan 2017–Feb 2025 vintage originally
pinned, but **it stops six months before collection begins**, so as of
2026-08 the overlap is zero and no accuracy claim is possible.

That gap is not a defect here and cannot be closed from this repository — it
closes when ONS publish a newer vintage of the ad hoc release. The monthly
backfill workflow re-runs discovery and picks one up automatically, and the
digest reports the remaining gap every month, so it is tracked rather than
assumed. Confirm what is currently loaded with the coverage query in
[Backfilling](#backfilling-onss-published-series).

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
python -m pytest                                    # 282 tests, no network
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
│   ├── backfill.py     Loads ONS published series (the answer key)
│   ├── index.py        Matched-sample relatives, splicing, rebasing
│   ├── reconcile.py    Monthly reconstruction (Task 4)
│   ├── validate.py     MAE/bias scoring (Task 6)
│   ├── digest.py       Monthly report — also what keeps the schedules alive
│   ├── export.py       Analytics JSON — how data leaves BigQuery
│   └── providers/      base.py · serpapi.py · travelpayouts.py · mock.py
└── tests/              339 tests, no network required
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

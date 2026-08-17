# UK Accommodation Price Nowcasting Pipeline

Reconstructs the ONS CPI/CPIH accommodation sub-indices (item class **11.2.0.1**,
"Hotels, motels, inns and similar accommodation services") ahead of publication —
not by forecasting rates, but by capturing the same forward-looking price
snapshots ONS collect, at the same lead time, for the same nights.

Data lands in BigQuery as an append-only, fully vintaged panel. Orchestration is
GitHub Actions cron. No hotel or OTA websites are scraped.

Sibling project: [`uk-airfares/`](../uk-airfares) reconstructs CPI item 07.3.3 the
same way. The architecture, failure policy, schema style and orchestration are
carried over deliberately. [Where the two diverge, and why](#where-this-diverges-from-the-air-fares-project),
is the most useful section here if you already know that project.

---

## Read this first: what this pipeline cannot currently tell you

Six limitations bound every number that comes out of this. Five are structural
rather than bugs, and three of them may never fully resolve.

**1. No collection has happened yet, so no accuracy claim is possible.** The
pipeline is complete and tested (191 tests, no network) but has never run against
the live provider. `validate` returns `INSUFFICIENT_DATA` and will keep doing so
until a full quarter of overlap exists. That is enforced, not advisory.

**2. Cancellation policy is uncontrolled, and this is the most serious
limitation.** Refundable and non-refundable rates for an identical room
routinely differ by **30–40%**, which makes holding cancellation policy constant
the single most important contamination control in accommodation data. It cannot
be applied on this source. A raw-key census over 214 live properties found
`free_cancellation` in the key set of **none** of them; the only route is a
nested `prices` array carried by ~17%, giving a known value for about 6%. With
the control switched on, the filter rejected 100% of every cell and the pipeline
produced no panel at all.

So the series is collected with `RATE_BASIS=any` — a deliberate decision, taken
on that evidence, to accept a known contamination rather than collect nothing.
The bias is real and its *sign is not fixed*: it depends on how the refundable
mix shifts month to month, which is exactly the kind of movement a nowcast would
otherwise attribute to the market. The control code is intact and correct — set
`RATE_BASIS=free_cancellation` and it applies — so this resolves if a source
that reports the field is ever adopted.

It is treated as a standing defect, not a footnote: `rate_basis` is on every
row, `validate.py` raises a permanent blocker while it is `any`, and the monthly
digest restates it.

**The direct consequence: the validation verdict is capped at `PROVISIONAL`.**
`SCORED` means "enough overlap *with clean provenance*", and provenance is not
clean while a known contamination of unquantified size sits in the series. So
this pipeline cannot reach `SCORED` on the current source however well it
tracks ONS — and that is the intended behaviour, not an oversight to route
around.

**3. Board basis and room type are unknown on every row.** Same cause, second
instance. Google Hotels returns a property's lowest available rate without
saying whether it includes breakfast or which room it is for, so room-only and
breakfast-inclusive rates are mixed together too. Recorded as
`board_basis = NULL` and `comparability_basis LIKE '%board=unknown%'` on every
row, and likewise a permanent validation blocker.

**Of the four contamination controls the methodology calls for, one is
applied** (room and occupancy, fixed at the query). Taxes are handled by storing
both bases. The other two are unavailable.

**4. The regional weights are placeholders and probably always will be.** ONS
publish a CPI weight for class 11.2.0.1 as a whole, and regional expenditure
weights in aggregate, but not the cross-tabulation needed to weight twelve
regional sub-indices into one national figure *for this item*. The committed
`weights.csv` is population-proportional, which is emphatically not expenditure,
and is flagged `is_placeholder`. `load_weights()` refuses to hand placeholders to
the validation path. **Per-region reconstructions do not use weights at all and
are unaffected** — only the national `location = "all"` roll-up is, and it is
explicitly a convenience level rather than the headline.

**5. The published series has two methodology breaks, both recent.** See
[The methodology being replicated](#the-methodology-being-replicated). The item
was rebuilt in 2025 and again in February 2026. A value from 2024 and a value
from 2026 are not measurements of the same thing, so comparisons never span a
break — which cuts the usable answer key down sharply.

**6. The answer key is short.** The regional ad hoc release begins in January
2025 because the six-weeks-ahead item began then. Its published coverage at time
of writing runs to July 2025. The national time series has decades of history but
covers all of 11.2.0.1 including items we do not replicate, so agreement with it
is weaker evidence.

The pipeline is designed to make all six visible rather than to paper over them.

---

## The methodology being replicated

From the ONS *Consumer price inflation basket of goods and services* articles
(2025 and 2026), the *Special case aggregates in consumer prices* methodology
page, and the *Traditional data aggregates in consumer prices* page.

**This item has been rebuilt twice in nineteen months, and only the third design
is live:**

| Era | Design |
|---|---|
| **Before 2025** | One item: an overnight stay on index day, priced **the day before the stay** by internet and phone. Notoriously volatile — collectors were pricing last-minute inventory, and sampled hotels were sometimes simply full, leaving nothing to price. |
| **2025** | A second item added on the same method but priced **six weeks in advance**, with the existing item's weight split across the two. Intent: more availability, less short-term demand pressure. |
| **2026 (live)** | The one-day-ahead item **removed from the basket**. The six-weeks-ahead item now prices **two separate nights each month** — one in index week, one the Thursday after index week, the second chosen to sit far enough from index day that event-specific spikes have passed. The second night began with February 2026 data. |

So the live methodology is: **one advance window of six weeks, two one-night
weeknight stays per CPI month.**

Accommodation has **not** migrated to web-scraped or alternative data. The ONS
transformation programme has taken rail fares, second-hand cars, groceries
(scanner data, from February 2026 data onward) and private rents. This item is
still traditional collection — by phone, email and online, by head-office staff,
as part of the *regional services collection*, which is also why it is priced and
published regionally.

### Index day means something different here

Index day is still the 2nd or 3rd Tuesday, still withheld in advance, still
confirmed retrospectively in the following month's bulletin — that much carries
over from air fares unchanged, along with the bulletin parser and its structural
check.

What changes is what it anchors. For air fares, index day is when the collector
works *and* when the flight departs. Here it is **neither**: it is the night
being stayed in. Collection happened six weeks earlier.

The consequences run through everything:

- **The collection schedule sits six weeks before the month it measures.** To
  reconstruct August, we collect in late June and early July.
- **A mis-parsed index day cannot cause a missed collection** — that already
  happened, six weeks ago. It causes the wrong rows to be attributed to the wrong
  month, which is quieter and therefore worse.
- **Reconciliation reaches backwards two months** into the panel, not into the
  current one.

### Both sampled nights are weeknights

Tuesday (index week) and the Thursday nine days later. There is no weekend leg.
That is a genuine surprise — hotel pricing is strongly weekday/weekend-segmented
and ONS deliberately sample only the weekday side — and it is asserted in a test
so it cannot be "corrected" back into a weekend pattern by someone assuming one
must exist.

### One thing that is genuinely unresolved

"Collected six weeks in advance … for two separate nights … at the same time"
does not pin down whether the two nights share **one** collection day or each
gets its **own** six-week lead. Both readings are defensible from the published
wording and they differ by nine days of price drift.

Rather than guess, both are collected and both are computed:
`collection_alignment` is `per_night` or `single_day` on every row, and
validation scores them side by side. The same treatment is applied to the four
other open questions — see [Variants](#variants-nothing-is-guessed-at).

---

## Where this diverges from the air-fares project

Everything below is a deliberate departure, not drift. If you are porting a
change between the two projects, this is the list to check it against.

| | Air fares | Accommodation | Why |
|---|---|---|---|
| **What index day anchors** | Collection *and* departure | The **stay night** only | The item is priced six weeks ahead |
| **Collection schedule** | Daily across the 8th–21st, inside the index month | 4–6 computed dates, ~6 weeks *before* the index month | Follows from the above |
| **Advance windows** | 1, 3, 6 months by haul | **42 days**, one window | ONS collect one window for this item |
| **Panel unit** | Route (stable forever) | Location **and** property (property churns constantly) | A hotel closes; LHR–JFK does not |
| **Selection rule** | Flight nearest a fixed target time | A **fixed sampled property**, priced whatever it costs | The ONS analogue of a price-blind rule |
| **Outlier cap anchor** | 5× the **cheapest** comparable | 5× either side of the **median** | Hotel result sets fail low as well as high — see below |
| **Row grain** | One row per route × window | One row per location × night × **property** | The provider returns a whole location per call |
| **Query cost** | 44 calls/day, 23 routes | 12–24 calls per collection day, 12 regions | Properties are free after the location call |
| **Answer key** | One ad hoc release | **Three** sources, kept strictly separate | See [The answer key](#the-answer-key) |
| **Series breaks** | 9 missing lockdown months | 2 **methodology** breaks (2025, Feb 2026) | Different cause, same disqualifying effect |

### The outlier-cap divergence, which is a genuine correction

The air-fares filter caps candidates at 5× the *cheapest* comparable. That anchor
works there because a nonsense fare is always nonsense on the high side — a
connecting itinerary at 67× the direct fare.

Hotel search results fail in **both** directions. A hostel dorm bed, a mislabelled
room or a plain data error puts an absurdly *low* rate into the set, and anchoring
on the minimum then lets that one bad rate evict every legitimate property in the
cell: a £1 listing alongside a £140 four-star would leave the £1 as the only
survivor, which is precisely backwards. This was caught by a test during the
build, not in production.

The cap here anchors on the **median** and applies in both directions. The median
is robust to a single bad value at either end, still catches the absurd high
outlier, and cannot be dragged by the thing it is meant to reject.

---

## Comparability: the four controls, and which two we can apply

The ONS-style selection rule is price-blind by design, so it is only safe over a
genuinely comparable candidate set. A hotel search returns *everything in the
city* — hostels, serviced apartments, whole-home rentals, five-star suites — each
with a headline nightly rate, each looking like a valid observation. And a single
property offers different rates for the same night depending on cancellation
terms, board and room type.

| Control | Status |
|---|---|
| **Cancellation policy** | **Not applied — see limitation 2.** The source does not expose it: absent from the key set of all 214 live properties surveyed. Holding it constant rejected 100% of every cell, so the series runs `RATE_BASIS=any` with the contamination recorded and blocked on rather than hidden. |
| **Room and occupancy** | **Applied, at the query.** Two adults, no children, one night, every call, every month. These are constants in `panel.py`, not configuration, so they cannot drift mid-series. |
| **Taxes and fees** | **Applied, by storing both.** `price_gbp` follows the configured `tax_basis`; `price_before_taxes_gbp` is kept alongside. So a series collected under one basis can be recomputed under the other without re-collecting. |
| **Board basis and room type** | **NOT APPLIED.** Not reported by any implemented provider. See limitation 2 at the top. |

Behind those sits the median-anchored outlier cap, plus a property-type filter
(vacation rentals excluded — a whole flat sleeping six is not a comparable to a
double room) and the star-tier filter.

Every row records `comparability_basis`, `n_considered`, and a breakdown of what
was dropped and why (`n_dropped_rate_basis`, `n_dropped_tier`,
`n_dropped_property_type`, `n_dropped_outlier`), plus
`cell_price_spread_ratio` — dearest over cheapest within the surviving set. If
that stays large after filtering, the set is not comparable and the filter needs
another control, not the index another caveat. That diagnostic is what caught the
equivalent bug on the air-fares panel.

---

## Panels

### Locations — twelve, one per ONS region

ONS price this item regionally and the ad hoc release publishes it regionally, so
region is the dimension ONS actually sample on and the one mirrored here.

| Region | City | Region | City |
|---|---|---|---|
| North East | Newcastle upon Tyne | London | London |
| North West | Manchester | South East | Brighton |
| Yorkshire and The Humber | Leeds | South West | Bristol |
| East Midlands | Nottingham | Wales | Cardiff |
| West Midlands | Birmingham | Scotland | Edinburgh |
| East of England | Cambridge | Northern Ireland | Belfast |

Each carries a `rationale` so the sample's composition stays auditable.

### Properties — discovered, pinned, and allowed to churn

ONS re-price a fixed sample and substitute when one drops out. `discover.py`
mirrors that: it draws a sample per (region, star tier) **price-blind** (by token
order — drawing by price would make every later month a comparison against a base
chosen for being cheap), pins it in `data/property_panel.csv`, and substitutes
only when the `property_churn` view reports a property has left. Substitutions are
recorded as `substitute_for:<old token>`, never made silently.

Collection records **every** comparable property, not just the pinned ones, and
marks pinned ones with `is_panel_property`. That gives two sample rules —
`pinned_panel` and `matched_census` — computed side by side, so a thinning pinned
sample cannot quietly shrink the index.

**The panel file starts empty**, and that is fine: until discovery runs, the
pipeline collects in census-only mode, which still produces a defensible
matched-sample index. Refusing to collect for want of a pinned sample would lose
stay nights that can never be re-priced.

Star tiers are `midscale` (3–3.5★) and `upscale` (4–4.5★). Unrated, 1–2★ and 5★
properties fall outside both — deliberately, since none has a comparable product
across all twelve regions.

---

## Variants: nothing is guessed at

Five things about this item's methodology are genuinely unresolved from public
sources, and each one changes the answer. Every combination is computed and
tagged; validation scores them side by side.

| Dimension | Values | Stakes |
|---|---|---|
| `attribution_rule` | `stay_month`, `collection_month` | **Highest.** A six-week lead puts these one to two whole months apart, nearly always — not an occasional boundary case as for air fares |
| `collection_alignment` | `per_night`, `single_day` | Nine days of price drift |
| `sample_rule` | `pinned_panel`, `matched_census` | Property churn |
| `agg_method` | `mean`, `median`, `geometric_mean` | Jevons is the CPI default and most likely; all three carried |
| scope | per-tier / pooled, per-night / pooled | ONS publish one item covering both nights, but they are separate measurements |

That is a lot of rows per month, and it is still the right trade: a reconstruction
that cannot say which reading of the methodology produced it is not evidence of
anything.

---

## The answer key

Three sources, kept strictly separate in `ons_published_index` via
`series_source`. They are on different bases and different geographies and
**must never be compared with one another** — doing so is the most obvious way to
produce a confidently wrong number here.

| Source | What it is | Basis | Geography |
|---|---|---|---|
| `adhoc_regional` | ONS ad hoc "Hotel overnight stays booked in advance: consumer prices sub-indices" — the six-weeks-ahead item specifically. **The primary target.** | January 2025 = 100 | 12 regions |
| `timeseries` | Published CPI item-class indices: `l7ie` (11.2.0.1), `l7ig` (11.2.0.2), and the 11.2 aggregate. Machine-readable JSON, no spreadsheet parsing. | 2015 = 100 | National |
| `price_quotes` | "Consumption segment indices and price quotes" microdata (renamed from "item indices and price quotes"), COICOP divisions 3–12, locally collected. Accommodation is division 11 and regionally collected, so quotes should be present. | — | — |

**Note that 11.2.0.2 is a separate class, not a subdivision of 11.2.0.1.** It
covers holiday centres, camping sites and youth hostels. Pooling them would be
wrong; `coicop_class` is on every row so they cannot be.

### Coverage is counted, never quoted

Every loader reports what actually landed — row count, distinct months, first and
last month, calendar span, per source — instead of repeating what the release says
it contains. On the sibling project a release titled as covering 2007–2026
actually loaded 2016-01 to 2026-02, and only counting caught it. The stated
coverage period is a title; the row count is a fact.

### Releases are ranked by coverage period, never by reference number

ONS restarted their ad hoc numbering, so the old five-digit series sorts
numerically *above* the newer four-digit one. On the air-fares build, ranking by
reference number picked a release three years out of date. `coverage_end()` parses
the period out of the URL slug instead, and there is a test asserting the newer
release wins despite the lower number.

---

## Choosing a price source

Researched August 2026.

| Source | Verdict |
|---|---|
| **SerpApi Google Hotels** ← *chosen* | No booking intent required. Exact check-in/check-out and occupancy. Returns `hotel_class` (a display string, plus a numeric `extracted_hotel_class`) and **both** `rate_per_night.lowest` and `before_taxes_fees`, which is what lets this pipeline store both tax bases rather than silently picking one. It does **not** return `free_cancellation` — see limitation 2. ~$25/month at this volume (250 searches free, $25 for 1,000); this panel needs ~24–48 calls a month. Same vendor, account and secret as the air-fares project. |
| **Booking.com Demand API** | Application, manual review, partner approval. |
| **Expedia Rapid (EPS)** | Formal partnership, minimum performance commitments, 3–6 month certification before going live. |
| **Hotelbeds APItude** | Certification and credential approval; bedbank terms written around booking volume. |
| **RateHawk / Emerging Travel** | Same partner-gated shape. |
| **LiteAPI, Makcorps, Travelpayouts** | Self-serve, but either cache-backed, thin on the fields the comparability filter needs, or both. |

**The three big ones are all structured around a look-to-book expectation.** A
research account that searches forever and books never is exactly the profile
those terms exist to stop. Building on one would mean building on an account
liable to be capped or closed — a worse outcome than a slightly less clean data
source — so they were ruled out rather than risked.

**Honest caveats on the chosen source.** SerpApi fetches Google Hotels rather than
holding a licence to it, and that model is under active litigation. We scrape no
hotel or OTA site ourselves, but this is one intermediary away from it and should
be understood as such. And the adapter is **written from documentation, not
verified against the live API** — serpapi.com is blocked by egress policy in the
development sandbox, exactly as it was when the air-fares adapter was written.
Parsing is defensive throughout and `raw_response` retains the full payload, so if
the shape differs the observations can be reparsed without re-querying. Check
`n_quotes` on the first live run before trusting it.

---

## Setup

### 1. BigQuery

```bash
export GCP_PROJECT=your-project
export BQ_DATASET=accommodation
export BQ_LOCATION=europe-west2   # London. Immutable once the dataset exists.
PYTHONPATH=src python -m ukhotels.ensure_tables
```

Creates the dataset if absent, then `accommodation_scrapes` (partitioned by
`scrape_date`, clustered by `location, stay_night_kind, property_tier`),
`reconstructed_index`, `ons_published_index`, and the `current_scrapes` and
`property_churn` views. All **append-only** — see [Invariants](#invariants).
Idempotent, so the workflows run it before every job.

You need a GCP project with **billing enabled** (BigQuery requires a billing
account even for the free tier) and the BigQuery API on. Usage here is a few tens
of MB a year against a 10 GB free allowance.

### 2. GitHub secrets

| Secret | Purpose |
|---|---|
| `GCP_PROJECT` | BigQuery project ID |
| `BQ_DATASET` | BigQuery dataset name |
| `GCP_WIF_PROVIDER` | Workload Identity provider resource name (keyless auth) |
| `GCP_SA_EMAIL` | Service-account email. Needs `bigquery.dataEditor` + `bigquery.jobUser` |
| `SERPAPI_KEY` | SerpApi API key |

Optional repository *variables*: `HOTEL_PROVIDER`, `RATE_BASIS`, `TAX_BASIS`,
`COLLECTION_ALIGNMENT`, `BQ_DATASET`.

Nothing is read from a committed file, and there is **no service-account key**.
Authentication uses Workload Identity Federation: GitHub mints a short-lived OIDC
token per run, so no long-lived secret exists to leak or rotate. This is also
required in practice — Google applies
`constraints/iam.disableServiceAccountKeyCreation` by default to new projects,
which blocks JSON key creation outright. Do not ask for that policy to be relaxed.

One-time setup (substitute your project and repo):

```bash
PROJECT_ID=your-project
REPO=owner/repo
SA=hotels-pipeline@$PROJECT_ID.iam.gserviceaccount.com
NUM=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud services enable iamcredentials.googleapis.com sts.googleapis.com

gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'"

gcloud iam service-accounts add-iam-policy-binding $SA \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"

echo "GCP_WIF_PROVIDER = projects/$NUM/locations/global/workloadIdentityPools/github/providers/github-provider"
```

The `attribute-condition` is what stops any other repository on GitHub exchanging
a token for your credentials — do not omit it.

### 3. Verify without spending anything

```bash
pip install -r requirements-dev.txt
python -m pytest                          # 191 tests, no network

# 2026-06-30 is exactly six weeks before the 2nd Tuesday of August 2026.
DRY_RUN=1 HOTEL_PROVIDER=mock PYTHONPATH=src \
  python -m ukhotels.pull --scrape-date 2026-06-30 --dry-run-out /tmp/dry.ndjson
```

The mock provider generates a deliberately *contaminated* property mix — vacation
rentals, unrated properties, five-star outliers, both cancellation bases — so the
comparability filter is exercised rather than bypassed.

---

## Running

```bash
# One collection run (no-ops unless something is due today)
PYTHONPATH=src python -m ukhotels.pull

# Draw or top up the pinned property sample
PYTHONPATH=src python -m ukhotels.discover --per-cell 5

# Load the answer key
PYTHONPATH=src python -m ukhotels.backfill --discover

# Reconstruct a month once ONS confirm the index day
PYTHONPATH=src python -m ukhotels.reconcile --index-month 2026-08

# Bypass the bulletin parser if you have read the index day yourself
PYTHONPATH=src python -m ukhotels.reconcile --index-month 2026-08 --index-day 2026-08-11

# Score against published ONS values
PYTHONPATH=src python -m ukhotels.validate --series-source adhoc_regional

# Monthly report and analytics export
PYTHONPATH=src python -m ukhotels.digest --month 2026-07
PYTHONPATH=src python -m ukhotels.export
```

### Schedules

| Workflow | Cadence | Behaviour |
|---|---|---|
| `hotels-collect` | `0 9 * * *` | Asks the calendar whether anything is due; no-ops on the ~25 days a month when nothing is |
| `hotels-monthly-reconcile` | `0 12 15-25 * *` | Attempts daily; no-ops until the bulletin is out |
| `hotels-backfill-ons` | `0 6 3 * *` | Refreshes the published answer key from both sources |
| `hotels-panel-refresh` | `0 8 4 * *` | Tops up and substitutes the pinned property sample; **commits** it |
| `hotels-monthly-digest` | `0 7 2 * *` | Writes and **commits** `reports/YYYY-MM.md` plus the analytics export |
| `hotels-ci` | on push/PR | Tests + an end-to-end mock dry run |

The collection gate is evaluated **in Python inside the job**, not in cron. The
due dates depend on which Tuesday turns out to be index day — withheld until the
following month's bulletin — so both hypotheses are live, giving four to six due
dates per CPI month scattered across two calendar months. That is not expressible
in cron, and maintaining the arithmetic in two places would guarantee they drift.

### Failure policy

| Condition | Behaviour |
|---|---|
| One location/night fails | Retry once with backoff, then write an `error` row and continue |
| Failure rate > `FAILURE_THRESHOLD` (default 34%) | Exit **1** — the vintage is not trustworthy |
| Nothing due today | Exit **0** with a notice — the expected case most days |
| Bad config / missing key | Exit **2** immediately, before querying anything |
| Bulletin not published yet | Exit **0** with a notice — expected, not an error |
| Bulletin published but unparseable | Exit **1** — our parser broke, and silence would skip the month forever |
| Index month predates the panel | Exit **0** with a notice — permanent absence, nothing to fix |
| Collection dates missing *during* active collection | Exit **1** — the collector broke |

The last two look identical from inside a failing reconcile and are opposite in
meaning, so `reconcile` checks `MIN(scrape_date)` before deciding which it is.
That check exists because the first weeks would otherwise be a wall of red runs,
and six red runs for an absence you cannot fix is how someone learns to ignore
Actions email — which the 60-day trap below makes expensive.

**The failure rate is measured over cells, not rows.** A successful cell yields
several property rows and a failed one yields exactly one, so a row-based rate
would report a half-failed run as a ~10% failure and slip under the threshold.
There is a test for that.

Failures are written to BigQuery as rows, not merely logged. An absent row and a
failed row are different facts, and only one of them is recoverable later.

---

## The monthly digest, and the 60-day trap it exists to defuse

`hotels-monthly-digest` writes `reports/YYYY-MM.md`. Read that one file and you
have caught up.

It also solves a problem that would otherwise kill this pipeline quietly, about
two months after you stopped watching it:

> **GitHub disables scheduled workflows after 60 days of repository inactivity —
> and workflow runs do not count as activity. Only commits do.**

So the failure mode is specifically the success case. A pipeline that collects
perfectly, needs no attention and therefore receives no commits gets switched off
on day 60. One email, easy to miss.

**The stakes are higher here than for air fares.** A missed collection day is a
stay night that can never be priced — the rate was a quote six weeks ahead of a
night that has now passed, and nobody retains it. And because this item's
collection calendar is only four to six days per CPI month, losing two months of
schedule loses most of two months of index.

Committing the digest is a real commit on a monthly cadence, so the counter never
gets past ~30 days. The panel-refresh workflow commits too, giving a second
independent reset. The report that tells you the pipeline is healthy is the same
thing keeping it alive.

### Why this workflow's failure posture is inverted

Everything else here fails loudly and early. This one does the opposite, on
purpose: **it must always reach its commit step.**

- `ukhotels.digest` wraps each query individually, so a failure becomes a note in
  the report rather than an exception.
- **A failed query is itself recorded as a concern.** The first real digest on the
  sibling project printed "Nothing flagged. Collection healthy." underneath two
  sections reading "unavailable: NotFound" — the health checks sat inside the
  success branch, so a failed query meant nothing was ever checked. A report that
  cannot see the data must never conclude the data is fine. There is a test named
  after exactly that.
- If generation fails outright, the workflow commits a placeholder saying so.
- Only after committing does it exit non-zero, so the run still shows red.

### Getting the data out of BigQuery

`reports/data/analytics.json`, written by the same workflow, is the export for
anything that wants the numbers without cloud access.

It exists because **BigQuery cannot be queried from outside a workflow run.**
Service-account JSON keys are blocked by org policy, and the WIF token that
replaced them is minted from GitHub's OIDC provider and exists only inside a
running job. The network reaches `bigquery.googleapis.com` fine; the credential is
the wall, and it is deliberate. So the data leaves the same way it arrived.

**Aggregates only, never raw rows.** Git keeps every version of everything. The
export also carries a `methodology` block recording the `rate_basis`, `tax_basis`
and `collection_alignment` the panel was collected under — without those the
numbers are uninterpretable, and nothing else in the file would say which series
it is. Output is `sort_keys`'d, so a month with no new data produces a
byte-identical file and therefore no commit.

---

## Making it like-for-like with ONS

Our reconstruction produces a **mean nightly rate in pounds** (~£120). ONS publish
an **index number**. These are not comparable in level and never will be — we are
not sampling the same properties, room types or board bases. Any attempt to match
levels would be measuring our sample composition, not the market.

So don't. Contribute only the *change*, and take the level from ONS:

```
nowcast_level(m) = ONS published_level(m-1) × our price_relative(m-1 → m)
```

This is a **splice**. Like-for-like in the only sense that matters — both sides
are a month-on-month price relative for the same CPI item — and the output is a
level on ONS's own basis. `validate.py` reports its error as
`splice_mae_index_points`.

### Matched samples matter more here than for air fares

The air-fares project matches on route, and routes are stable, so an unmatched
aggregate is merely risky. Accommodation has no stable unit below the location. A
property closing, rebranding, leaving the aggregator or simply being full on the
night we price is **the normal monthly condition** — ONS's own 2025 and 2026
changes were made precisely because sampled hotels being fully booked left nothing
to price.

An unmatched average would manufacture large phantom movements every single month.
There is a test showing the naive version inventing a 25% "price collapse" out of
one property closing for refurbishment. Relatives are computed only over
properties priced in both months, keyed on the provider's stable token — a rebrand
changes the name and keeps the token, and matching on names would read one rebrand
as a property leaving and a different one arriving.

`build_chained_index` **breaks the series** rather than carrying a level forward
when a month cannot be chained. A fabricated level that looks like real data is
worse than a visible gap.

### Elementary aggregate formula

| Formula | Definition | Note |
|---|---|---|
| **Jevons** | geometric mean of relatives | CPI default for most items |
| **Dutot** | ratio of arithmetic means | dominated by expensive properties |
| **Carli** | arithmetic mean of relatives | known upward bias; what a naive implementation does |

All three computed and tagged; validation settles it.

---

## Validation

`validate.py` is deliberately hard to get a favourable answer out of. Guards, in
the order they bite:

1. **Minimum overlap, counted in *consecutive published* months.** Under one full
   quarter → `INSUFFICIENT_DATA`, no headline MAE. Three months scattered either
   side of a gap support zero usable month-on-month comparisons.
2. **Methodology breaks are not crossed.** A relative spanning the 2025 or
   February 2026 break is a change of measurement, not a price movement. This is
   the accommodation analogue of the air-fares project skipping its nine missing
   lockdown months — different cause, same disqualification.
3. **Rolling origin.** Each month scored on what was knowable before it. An
   in-sample average would flatter the pipeline.
4. **Variant-selection honesty.** Whichever variant scores best was chosen *after*
   seeing the answers, so its MAE is optimistically biased — the report says so
   and states how many variants were in the running.
5. **Provenance blockers.** Placeholder weights, a cache-backed source, a
   substituted collection date, property churn, mixed methodology eras, or —
   always, today — unknown board basis each downgrade the verdict regardless of
   the numbers.

Errors are in **percentage points of month-on-month change**, not levels.

Verdicts are `INSUFFICIENT_DATA` → `PROVISIONAL` → `SCORED`.

On the current source the ceiling is **`PROVISIONAL`**, permanently, because
cancellation policy is uncontrolled and board basis is unknown (limitations 2
and 3). `SCORED` requires clean provenance, and this series does not have it.
Reaching `SCORED` needs a source that reports those fields, not a change here.

So the honest reading of a good result is: *the movements track ONS closely,
with a known contamination of unquantified size in the underlying rates.* That
is still a useful nowcast. It is not a validated one.

---

## Invariants

- **Query `current_scrapes`, audit `accommodation_scrapes`.** A date can carry
  several runs (a retry, a re-run, a double-click). The view exposes the latest
  run per date — one coherent vintage — and matches what reconciliation uses.
- **`accommodation_scrapes` is never UPDATEd and never DELETEd from.** Every pull
  is a new vintage. If a rate looks wrong the fix is another row, not an edit. A
  contaminated run stays in the table as the evidence for whatever fix it
  prompted; queries exclude it via the view. There are tests asserting no
  mutating statement exists in any SQL file.
- **`reconstructed_index` is likewise append-only.** A month legitimately gains
  rows over time. `computed_ts` orders vintages; `is_current` marks the latest.
- **SQL files are submitted whole, never split on `;`.** The column descriptions
  contain semicolons, so a naive split severs string literals and every statement
  fails with an unclosed literal. BigQuery executes multi-statement scripts
  natively. There is a test asserting `bq.py` contains no such split.
- **Load jobs, not streaming inserts.** Cheaper, and rows are immediately
  queryable and partition-prunable.
- **No PII, ever.** Location, dates, occupancy and property identity only. No
  logins, no loyalty numbers, no guest names. Property names are business names.

---

## Layout

```
uk-hotels/
├── sql/
│   ├── 001_accommodation_scrapes.sql   Append-only observation panel
│   ├── 002_reconstructed_index.sql     Monthly reconstructions, one row per variant
│   ├── 003_ons_published_index.sql     ONS's own values — the answer key
│   ├── 004_current_scrapes_view.sql    Latest coherent vintage per date
│   └── 005_property_churn_view.sql     Which properties have left the sample
├── reports/                            Monthly digests (committed by Actions)
│   └── data/analytics.json             Analytics export (committed by Actions)
├── src/ukhotels/
│   ├── onscal.py       Collection calendar — where this diverges most from air fares
│   ├── panel.py        Locations, pinned properties, weights
│   ├── selection.py    Comparability filter — the trap-1 module
│   ├── discover.py     Draws and maintains the pinned property sample
│   ├── config.py       Environment-driven config, including the methodology knobs
│   ├── bq.py           Append-only BigQuery writer + dry-run writer
│   ├── pull.py         Collection
│   ├── onsfetch.py     CPI bulletin index-day parser
│   ├── backfill.py     Loads the three published answer-key sources
│   ├── onsweights.py   Attempts regional weights; usually fails, by design
│   ├── index.py        Matched-sample relatives, splicing, methodology eras
│   ├── reconcile.py    Monthly reconstruction, all variants
│   ├── validate.py     MAE/bias scoring with the overlap and provenance guards
│   ├── digest.py       Monthly report — also what keeps the schedules alive
│   ├── export.py       Analytics JSON — how data leaves BigQuery
│   └── providers/      base.py · serpapi_hotels.py · mock.py
└── tests/              191 tests, no network required
```

## Non-goals

- Not scraping hotel or OTA websites directly. API or licensed aggregator only.
- Not replicating ONS's exact property sample — it is not public. The goal is a
  well-calibrated proxy validated against ONS's own published values.
- Not storing personal or traveller data of any kind.
- Not claiming the pipeline "works" before a full quarter of overlap exists.

## Sources

- [ONS — Consumer price inflation basket of goods and services: 2026](https://www.ons.gov.uk/economy/inflationandpriceindices/articles/ukconsumerpriceinflationbasketofgoodsandservices/2026)
- [ONS — Consumer price inflation basket of goods and services: 2025](https://www.ons.gov.uk/economy/inflationandpriceindices/articles/ukconsumerpriceinflationbasketofgoodsandservices/2025)
- [ONS — Special case aggregates in consumer prices](https://www.ons.gov.uk/economy/inflationandpriceindices/methodologies/specialcaseaggregatesinconsumerprices)
- [ONS — Traditional data aggregates in consumer prices](https://www.ons.gov.uk/economy/inflationandpriceindices/methodologies/traditionaldataaggregatesinconsumerprices)
- [ONS ad hoc 2993 — Hotel overnight stays booked in advance: consumer prices sub-indices, January 2025 to July 2025](https://www.ons.gov.uk/economy/inflationandpriceindices/adhocs/2993hotelovernightstaysbookedinadvanceconsumerpricessubindicesjanuary2025tojuly2025)
- [ONS — CPI INDEX 11.2.0.1 Hotels, motels and similar accommodation services (L7IE)](https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/l7ie/mm23)
- [ONS — CPI INDEX 11.2.0.2 Holiday centres, camping sites, youth hostels (L7IG)](https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/l7ig/mm23)
- [ONS — Consumer price inflation consumption segment indices and price quotes](https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceindicescpiandretailpricesindexrpiitemindicesandpricequotes)
- [ONS — Changes to the provision of microdata outputs, January 2026](https://www.ons.gov.uk/economy/inflationandpriceindices/articles/changestotheprovisionofmicrodataoutputsforconsumerpriceinflationstatistics/january2026)
- [ONS FOI-2025-2494 — Guidance that ONS observers use to track consumer prices](https://www.ons.gov.uk/aboutus/transparencyandgovernance/freedomofinformationfoi/guidancethattheonsobserversusetotrackconsumerprices)
- [SerpApi — Google Hotels API](https://serpapi.com/google-hotels-api)

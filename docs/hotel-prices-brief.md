# Build brief: ONS Hotel / Accommodation Price Nowcasting Pipeline

*A starting prompt for a fresh Claude Code project. Paste the whole thing.*

*Written after building the air-fares equivalent (`uk-airfares/` in
`elpatcho01/Apps`). Everything under "Traps" is a mistake that was actually made
and cost real time — it is the most valuable part of this document.*

---

## Paste this

---

I want to build a **UK hotel / accommodation price nowcasting pipeline** that
replicates ONS's own price-collection methodology for the CPI/CPIH accommodation
item, so that sub-indices can be reconstructed before ONS publish them.

There is a working reference implementation of the same idea for **air fares** at
`github.com/elpatcho01/Apps`, directory `uk-airfares/`. **Read its README first
and read `src/ukairfares/onscal.py`, `reconcile.py`, `digest.py` and
`export.py`.** The architecture, failure policy, BigQuery schema style and
GitHub Actions orchestration should be carried over. This is a sibling project,
not a fresh design problem. Tell me where you deliberately diverge and why.

### Task 0 — Establish the facts before writing any code

Two separate research questions. **Do not proceed to Task 1 until both are
answered, and stop and ask me if either comes back badly.**

**0a. What is ONS's actual accommodation methodology, as of 2026?**

Do not assume it mirrors air fares. Establish from primary sources:

- The exact CPI/CPIH item(s). Accommodation services sits around COICOP
  **11.2.1**, but confirm the current item code, name, and whether hotels,
  B&Bs and short-term lets are separate items.
- **Has ONS already moved this item to an alternative/web-scraped data source?**
  There is published ONS research on using scraped accommodation prices. If they
  have migrated, find out *when* and *to what* — it changes what you are
  replicating and may mean the historical series has a methodology break in it.
- Is there an **index day** concept, as there is for air fares (2nd or 3rd
  Tuesday, confirmed retrospectively in the following month's CPI bulletin)? Or
  is accommodation collected across a window? Do not assume the air-fares answer.
- **Advance-purchase windows and stay patterns.** Air fares use 1/3/6 months
  ahead by haul. Find the accommodation equivalent: how far ahead, how many
  nights, which nights of the week.
- Check the **CPI/RPI Technical Manual** section on accommodation, and search the
  ONS FOI disclosure log — the air-fares methodology only came out via FOI
  (FOI-2023-1164), so the same is plausible here.

**0b. What is the answer key, and where does it come from?**

You need published ONS values to validate against, at the same granularity you
reconstruct. Two candidate sources — check both:

- **ONS "Consumer price inflation item indices and price quotes"** — a monthly
  dataset publishing item-level indices *and* the underlying price quotes for
  locally collected items. If accommodation is locally collected, this is a far
  better answer key than anything the air-fares project had, because you get
  actual quotes rather than only an index. Establish whether accommodation
  appears in it.
- An **ad hoc release**, which is how air fares are published. If you go this
  route: rank candidate releases by *coverage period parsed from the release
  slug*, never by ad hoc reference number — ONS restarted their numbering, and
  ranking by reference number picks a release four years out of date. This
  cost real time on the air-fares build.

Report what the answer key actually contains once loaded — **count the rows and
the distinct months yourself rather than trusting the release's stated coverage
period.** On the air-fares project the release claimed 2007–2026 and what
actually loaded was 2016-01 to 2026-02.

**0c. What is the price source, and is this use permitted?**

Amadeus Self-Service was decommissioned 17 July 2026 — do not build against it.
Research current options and report a recommendation with reasoning. Likely
candidates to investigate: **SerpApi Google Hotels** (the air-fares project uses
SerpApi Google Flights and it works well), Booking.com Demand API, Expedia Rapid,
LiteAPI, RateHawk / Emerging Travel, HotelBeds Apitude, Makcorps, Travelpayouts.

For each: free tier and real cost at this volume, rate limits, whether specific
check-in/check-out dates and occupancy can be requested, whether star rating and
board basis are returned, and **whether the ToS permit storing and republishing
derived index values**. Flag anything incompatible with a recurring automated
pull for research use. Several hotel APIs are partner-only or enforce a
look-to-book ratio — if the only viable options require booking intent we do not
have, **stop and tell me** rather than building against something that will get
the account closed.

No airline/OTA/hotel website scraping directly. API or licensed aggregator only.

### Traps — read this section twice

These are mistakes actually made on the air-fares build. Most cost hours.

**1. The price-blind selection rule will pick absurd products.** ONS's air-fares
rule is "the flight departing closest to a fixed target time, whatever it costs".
Applied naively it selected £4,841 LGW–EDI on SWISS — a connecting itinerary via
Zurich that the aggregator listed beside direct services. First day of live
collection produced a £1,244 average domestic fare, 1306% above the cheapest.

**The hotel equivalent is worse, because a single property returns many rates for
the same night.** Before applying any ONS-style selection rule, filter to a
comparable product set and hold all of these constant, recording what you
filtered on every row:

- **Refundable vs non-refundable** — often a 30–40% difference for the identical
  room. This is the single biggest contamination risk.
- **Board basis** — room only / B&B / half board / all inclusive.
- **Room type and occupancy** — a double for one adult is not a twin for two.
- **Taxes, VAT, city tax, resort fees** — decide advertised-price or
  total-price, apply it consistently, and record which. Mixing them silently is
  a permanent bias you cannot unpick later.

Also keep an outlier cap (the air-fares code uses 5× the cheapest comparable) and
store both the ONS-rule price and the cheapest, plus a diagnostic for how far
apart they are. On the air-fares panel that diagnostic is what caught the bug.

**2. Properties churn; routes do not.** LHR–JFK exists every month. A specific
hotel closes, rebrands, leaves the aggregator, or renovates and jumps two price
tiers. This is a real structural difference and it breaks matched-sample logic
that assumes a stable panel. You need stable property identity, explicit handling
of a property leaving, and month-on-month relatives computed on the **matched
sample only** — properties present in both months. Design for this from the
start; retrofitting it is painful.

**3. Never overwrite a row. Never delete a "bad" one either.** The panel is
append-only and every pull is a new vintage. When two contaminated runs landed
alongside a clean one, the fix was **a view exposing the latest run per
collection date** (`current_scrapes`), not a DELETE. Those bad rows are the only
evidence of the failure and the fares are gone forever. Analysis reads the view;
audit reads the table. Reconciliation takes the latest run for a date — otherwise
a duplicated day inflates the observation count and outvotes a clean one.

**4. GitHub disables scheduled workflows after 60 days of repository inactivity,
and workflow runs do not count as activity — only commits do.** So the failure
mode is the *success* case: a pipeline that runs perfectly and needs no
attention gets switched off around day 60, with one easily-missed email, and the
resulting data gap is unrecoverable. Solve it the way the air-fares project does:
a monthly digest workflow that generates a report **and commits it**. Copy
`digest.py` and its workflow. Note its failure posture is deliberately inverted
— it must always reach the commit step, so query failures degrade to notes in the
report and the run exits non-zero only *after* committing.

**5. A report that cannot see the data must never conclude the data is fine.**
The first live digest printed "Nothing flagged. Collection healthy." underneath
two sections reading "unavailable: NotFound". The health checks sat inside the
success branch, so a failed query meant nothing was ever checked. **An
unanswered question is itself a concern.**

**6. Distinguish "no data because we have not collected that period yet" from
"no data because the collector broke".** They look identical from inside a
failing reconciliation and mean opposite things. Reconcile attempts last month on
a daily schedule, so for the first few weeks it is always asked for a month older
than the panel — failing there produces a fortnight of red runs for an absence
that is expected and permanently unfixable. That is exactly how someone learns to
ignore Actions email, which is expensive given trap 4. Check the earliest
collection date before deciding which case you are in.

**7. Service-account JSON keys are blocked** by the
`iam.disableServiceAccountKeyCreation` org policy on new GCP projects. Do not
waste time on them and do not ask for the policy to be relaxed. Use **Workload
Identity Federation** — GitHub mints a short-lived OIDC token per run, no
long-lived credential exists. All the air-fares workflows do this; copy the auth
step verbatim.

A consequence worth planning for: **BigQuery is then unreachable from anywhere
except inside a workflow run.** The air-fares project solves this with an
`export.py` that runs in Actions and commits aggregate JSON to the repo, which
anything can then read without cloud access. Do the same, and export
**aggregates only, never raw rows** — git keeps every version of everything.

**8. Do not split SQL files on `;`.** Column descriptions contain semicolons, so
a naive split severs string literals and every statement fails with an unclosed
literal. BigQuery executes multi-statement scripts natively — submit each file
whole.

**9. Expect holes in the answer key, and expect them to be worse than for
flights.** The air-fares published series is missing nine months —
2020-04/05/06, 2020-11, 2021-02 through 06 — the UK lockdown windows, where ONS
suspended collection and imputed the item. **Hotels were legally closed for much
of that period, so assume the accommodation series is at least as gappy.** Two
consequences: rolling-origin validation must skip those months, and "a full
quarter of overlap" must mean three consecutive *published* months, not three
consecutive calendar months.

**10. When the methodology is genuinely ambiguous, compute every variant and let
the data decide.** Air fares left three things open and stored all of them:
attribution (does a price belong to the month of *stay* or the month of
*collection*?), the elementary aggregate formula (Jevons / Dutot / Carli), and
the exact target-time constant. Every reconstructed row is tagged with which
variant produced it, so validation scores them side by side. Do not guess and
bake one in.

### Tasks 1–8

1. **Location and property panel.** Weighted per ONS's own regional/tier weights
   if published; fetch them at runtime rather than committing guesses, and mark
   every aggregate computed with placeholder weights so it cannot be mistaken
   for a real one. Cover the dimensions ONS actually samples on — establish
   those in Task 0, but expect location, property class/star rating, and
   weekday vs weekend stays. Hold occupancy, room type, board basis and
   cancellation policy constant.

2. **BigQuery schema, append-only.** `accommodation_scrapes` partitioned by
   `scrape_date`, clustered by the dimensions you filter on most.
   `reconstructed_index` partitioned by `index_month`. A published-series table
   for the answer key. A `current_scrapes` view (trap 3). Retain the raw API
   response per row so observations can be reprocessed without re-querying —
   this has already paid for itself once. Idempotent `CREATE ... IF NOT EXISTS`
   migrations applied at the start of every run.

3. **Collector.** Provider abstraction behind a protocol so the source can be
   swapped by config, with a mock provider for tests and a guard preventing mock
   data from ever reaching the real table. One row per
   (property/location × stay pattern × advance window). Retry once with backoff;
   **log and continue on individual failures, and write the failure as a row** —
   an absent row and a failed row are different facts and only one is
   recoverable. Exit non-zero if the failure rate crosses a threshold, because a
   mostly-failed run is not a usable vintage.

4. **Monthly reconciliation.** Fetch the ONS bulletin, locate the confirmed
   collection date, pull the matching scrapes, compute the index. Validate any
   parsed date structurally (for air fares: it must be the 2nd or 3rd Tuesday —
   that one constraint rejects essentially every mis-parse). Exit 0 with a notice
   if the bulletin is not out yet; exit 1 if it is out and unparseable — silence
   there would skip a month forever.

5. **GitHub Actions orchestration.** Secrets as encrypted secrets, never
   committed. WIF for GCP. Fail loudly rather than skip silently — except the
   digest, per trap 4.

6. **Validation.** MAE and bias per series, rolling-origin discipline. **Refuse
   to report an accuracy verdict until at least one full quarter of overlap
   exists** — return `INSUFFICIENT_DATA` and mean it. Downgrade the verdict
   automatically when rows used placeholder weights, a substitute collection
   date, or a cache-backed price source.

7. **Monthly digest** (traps 4 and 5) and **analytics export** (trap 7).

8. **README that states the current limitations plainly** at the top, before
   anything else. What the pipeline cannot yet tell you is more important than
   what it can.

### Non-goals

- Not scraping hotel or OTA websites directly.
- Not replicating ONS's exact property sample — it is not public. The goal is a
  well-calibrated proxy validated against ONS's own published values.
- Not storing personal or traveller data of any kind. No logins, no loyalty
  numbers, no guest names, no PII. Location, dates, occupancy and property
  identity only.
- **Do not claim the pipeline "works" before a full quarter of overlap data
  exists.** Say `INSUFFICIENT_DATA` for as long as that is the true answer.

### How I want you to work

Correct me where the brief is wrong. On the air-fares build three of my stated
assumptions were wrong on the evidence — the collection dates, the return legs,
and the number of published series (six, not three) — and saying so was worth
more than implementing them faithfully. State the correction, then build.

Tests must run with no network access. Commit with real explanations of *why*,
not restatements of the diff.

---

## Notes for you (not part of the prompt)

- The single highest-value line is pointing the new session at
  `github.com/elpatcho01/Apps`, `uk-airfares/`. `onscal.py`, `digest.py`,
  `export.py` and the four workflow files are close to directly reusable.
- You will need the same secrets again: `GCP_PROJECT`, `GCP_WIF_PROVIDER`,
  `GCP_SA_EMAIL`, the provider API key, and a `BQ_DATASET` variable. The WIF
  setup in the air-fares README works as written.
- Use a **separate BigQuery dataset**, same project is fine.
- Budget: air fares runs ~700 API calls/month. Hotels will likely be higher,
  because the panel multiplies out across location × class × stay pattern ×
  advance window. Get an estimate in Task 0, before committing to a plan.

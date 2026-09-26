# Build brief: RPI Clothing & Footwear Nowcast and Carli-Wedge Measurement

*A starting prompt for a fresh Claude Code project. Paste the whole thing.*

*Sibling of the air-fares (`uk-airfares/`) and hotels (`uk-hotels/`) pipelines
in `elpatcho01/Apps`. Unlike those, this one is not a price-collection
replication problem first: workstream 1 needs no new data at all. The
"Traps" section combines mistakes actually made on the sibling builds with
ones specific to a Carli index. Read it before writing code.*

> **Two gaps in this draft, marked `[[TO FILL]]`.** The original request was cut
> off partway through workstream 2 ("LICENSED P…"), so the licensed-data
> specification and all of workstream 3 are missing. Fill them in before
> pasting, or paste as-is and let the session stop at those points. It is told
> to.

---

## Paste this

---

I want a **nowcasting and wedge-measurement pipeline for RPI Clothing &
Footwear**, ONS series **CHBJ** (Jan 1987 = 100). It has three workstreams in
priority order. The first uses only published ONS data and must ship before
anything else is started.

There is reference infrastructure at `github.com/elpatcho01/Apps`: `uk-airfares/`
and `uk-hotels/`. **Read `uk-airfares/README.md` first, then
`src/ukairfares/onscal.py`, `digest.py`, `export.py`, `validate.py` and the
workflow files.** Carry over the BigQuery append-only schema style, Workload
Identity Federation auth, the committed monthly digest and the analytics JSON
export. Carry over the validation discipline too: rolling origin, refuse to
score on thin overlap, and label a variant chosen after seeing the answers as
chosen after seeing the answers. Put the project in a new directory
`uk-rpi-clothing/`. Tell me where you deliberately diverge and why.

### What is already established. Do not re-derive it.

| Fact | Value |
|---|---|
| Target | CHBJ, RPI clothing & footwear. Jun 2026 = **325.0**, **+5.6% YoY** |
| RPI weight | **~42 parts per thousand**. Confirm against CZHJ (see Task 0) |
| CPI counterpart | D7BW = **118.7**, **−0.5% YoY** |
| Wedge (RPI − CPI YoY) | **5.99pp**, almost entirely the Carli formula effect |
| Scanner-data change, Feb 2026 | Groceries only. Clothing (COICOP division 03) untouched, so its Carli wedge survives intact and its **share** of the residual RPI−CPI formula wedge has risen |
| CRFV (ONS formula-effect series) | **Dead after Jan 2020.** The wedge has to be rebuilt bottom-up |
| Raw MoM sd | 2.21pp; **0.70pp** residual after seasonal |
| Current model MAE, 121 rolling origins Jun15–Jun25 (pp of index level) | h1 0.90 · h3 1.38 · h6 1.72 · h12 2.74 |
| 12-month drift | **Near-unforecastable.** No trailing window explains more than ~10% of next-12m growth variance. Error-correction and longer drift windows were tested and failed |
| Wedge persistence | Random-walk wedge: **h12 MAE 0.71pp vs 2.98pp** for the leg. A 76% reduction, from 12.5bp to 3.0bp of headline |

Seasonal factors, stated as year-demeaned, 2016–2026 excluding 2020/21 (pp of
MoM):

| Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|
| −3.60 | +4.25 | +2.24 | +0.87 | +0.76 | −0.60 | −2.19 | +1.88 | +3.22 | +0.80 | +0.72 | −0.58 |

### Task 0: confirm what needs confirming, then stop and report

Short, and **nothing in workstream 1 should be built before it is done.**

**0a. Sources and series.** Pin every series ID the pipeline reads and confirm
each one's basis, first observation and latest value against the table above.
Use the ONS time-series API (MM23 dataset) for CHBJ, D7BW, RPI all-items
(CHAW) and the RPI section weights. **Confirm the clothing & footwear weight
against CZHJ** and record the weight for every year in the sample, not just
2026. The weight changes each January, and the fixing-exposure numbers below
scale with it.

**0b. Numbers to reconcile before building on them.** Report each one, and
correct me if it is wrong:

- **The seasonal factors do not add up to zero.** They add up to **+7.77pp**.
  Year-demeaned factors should add up to ≈0 by construction. Either they carry
  the average annual drift (≈7.8%/yr), or the label is wrong. That changes the
  centre of every monthly contribution below, though not their spread. Find
  out which it is and state the corrected table.
- **Define the wedge exactly.** Say whether it is the difference of published
  1dp YoY rates, of YoY rates computed from 1dp index levels, or of log
  changes. The rounded published rates give 5.6 − (−0.5) = 6.1pp, not 5.99pp.
  These definitions differ by up to ~0.1pp, which is material against a
  0.71pp h12 MAE. Pick one, write it down, and use it everywhere.
- **Leg h12 MAE is quoted as 2.74pp in one place and 2.98pp in another.**
  Establish whether these come from different samples or different
  definitions. The 76% claim uses 2.98.

**0c. Public quote-level data.** ONS publish **"Consumer price inflation item
indices and price quotes"** monthly. It covers item-level indices *and* the
individual locally collected price quotes, with stratum and shop weights.
Establish:

- Whether clothing & footwear items are in it, from when, and with what
  coverage relative to the full sample (some quotes are suppressed or
  centrally collected).
- The fields actually present. From memory these include a price relative, a
  base price, stratum weight/type, region, shop type, and an indicator box
  flagging sale prices and comparable replacements. **Verify every one rather
  than trusting this list.**
- **Count the rows and distinct months you actually loaded.** Do not trust the
  release's stated coverage. On air fares a release titled 2007–2026 loaded
  2016-01 onward, because a year floor silently dropped earlier sheets.

This matters because it probably means **workstream 2's historical wedge can be
rebuilt without any licensed data at all**, and validated against CRFV over the
pre-2020 overlap. See workstream 2.

Stop and report after Task 0.

### Traps. Read this section twice.

**1. The fixing contribution is not weight × MoM.** RPI is a
January-linked chain. Within a year, all-items is a weighted sum of section
indices *relative to their January level*. So the section's contribution to
the all-items MoM is:

```
contrib_t = w × (I_c,t − I_c,t−1) / I_c,Jan  ÷  (I_all,t−1 / I_all,Jan)
```

It is not `w × MoM_c`. Clothing is low in January and rises through the
spring and autumn ranges, so its effective weight drifts by several percent
through the year. The bp figures in workstream 1 were computed as `w × MoM`
(42/1000 × −3.60 = −15.1bp). Recompute them properly. Report the effective
weight by month and how far the naive numbers are off.

**2. Full-sample seasonal factors are look-ahead.** The table above was
estimated on 2016–2026. Any rolling-origin evaluation must re-estimate the
seasonal factors, and the conditional variances in workstream 1, **using only
data available at each origin**. An evaluation that uses the full-sample table
will flatter every model scored with it.

**3. Nine observations per calendar month cannot support twelve free
variances.** Feb sd 0.97pp vs Oct 0.55pp looks decisive, but with n≈9 per
month an sd estimate carries roughly ±25% sampling error. A variance ratio of
~3.1 on (8, 8) degrees of freedom is borderline, not settled. Shrink the
monthly variances toward the pooled value (empirical Bayes, or a two- or
three-regime grouping such as sale-transition months Jan/Feb/Jul/Aug/Sep vs
the rest). Choose the amount of shrinkage out of sample. Report the
unshrunk, shrunk and regime versions side by side and score all three (trap
10).

**4. The Carli relative is against the January base price, not last month.**
The RPI elementary aggregate for clothing is the unweighted arithmetic mean of
`p_t / p_base` within each stratum. The base is reset each January. It is
**not** a chained mean of month-on-month relatives. That is exactly why the
Carli–Jevons gap *accumulates through the year and resets in January*.
Dispersion of `p_t / p_base` grows as quotes go on and off sale out of phase,
and Carli − Jevons ≈ ½·var(log relative). Sale bounce is the mechanism. A
quote in sale in January at 50% off that returns to full price has relative
2.0. A quote at full price in January that later goes 50% off has relative
0.5. Carli averages these to 1.25, Jevons to 1.0. Implement the base-price
convention exactly as ONS do, including base re-pricing for replacements, and
test it on this example.

**5. Replacements and imputation are most of the difficulty.** Clothing has
extreme product churn. Comparable replacements, non-comparable replacements
with quality adjustment, temporarily missing quotes and seasonal items all
have specific ONS handling rules (CPI/RPI Technical Manual). Getting these
wrong moves dispersion, and dispersion is the signal. Follow the manual, cite
the section per rule, and tag every reconstructed row with which handling
applied.

**6. The YoY wedge straddles two calendar years.** Because the chain links
each January, the YoY wedge at month *m* is the part-year wedge accumulated
Jan→*m* this year, plus the Jan-link effect, plus the part-year wedge *m*→Dec
of last year. Decompose it that way in the outputs. A random walk on the YoY
wedge silently mixes these, which is why it may be beatable at short horizons
and unbeatable at h12.

**7. "Almost entirely formula effect" should be measured every month.** RPI
and CPI clothing differ in formula, and also slightly in weights (household
coverage) and aggregation structure. The bottom-up reconstruction must produce
the non-formula residual explicitly, as its own series. Do not absorb it into
the Carli term.

**8. The 2020/21 hole.** Shops were legally closed and ONS imputed rather than
collected. The established figures exclude 2020/21, so keep that exclusion
everywhere: seasonal estimation, variance estimation, rolling origins and
quote-level reconstruction. On air fares the equivalent hole was nine
specific months (2020-04/05/06, 2020-11, 2021-02 to 06). Establish the
clothing-specific months from the quotes data rather than assuming, and make
any "quarter of overlap" gate count *published, collected* months.

**9. Egress: ons.gov.uk may be unreachable from the development sandbox.** On
air fares it was, so the only place that could see ONS was a GitHub Actions
run. Build a `--dump` mode into every ONS fetcher from day one, and test
parsers against committed fixtures so the suite runs with no network.

**10. When the method is ambiguous, compute every variant and let the data
decide.** Tag every output row with the variant that produced it: seasonal
estimator, variance shrinkage, replacement handling, wedge definition. Label
the best-scoring variant as selected after seeing the answers, and say how
many were in the running.

**11. Carried over verbatim from the sibling projects.** Append-only tables
with a `current_*` view, never UPDATE or DELETE. Don't split SQL on `;`. WIF,
not service-account keys. BigQuery is reachable only inside a workflow run,
so export aggregates to committed JSON. The monthly digest must commit (GitHub
disables schedules after 60 days without a commit) and must never say "healthy"
underneath a failed query. Compare contexts against literals in bash, not in
GitHub expressions. See the air-fares README for why each one exists.

### Workstream 1: monthly fixing exposure (no new data)

**Goal:** replace the current unconditional fan with a **calendar-month-
conditional** distribution of clothing's contribution to the monthly RPI
all-items change, in bp.

Starting numbers (naive `w × MoM`, to be recomputed per trap 1): Jan
−15.1bp, Feb +17.9bp, Sep +13.5bp, Mar +9.4bp, Jul −9.2bp, Aug +7.9bp.
Unconditional residual sd 2.9bp (4.8bp at p90). Within-month sd ranges from
0.97pp (Feb) to 0.55pp (Oct).

Build:

1. **Contribution engine.** The exact formula from trap 1, using the
   correct-year weight, applied to history and to forecasts.
2. **Conditional mean by month.** Seasonal factor plus drift. Drift is
   treated as unforecastable (see non-goals): use a simple trailing mean and
   carry its uncertainty; do not model it.
3. **Conditional variance by month,** shrunk (trap 3), with at least three
   variants scored.
4. **Fan by horizon.** For h = 1…12, the h-step variance is the sum of
   month-specific residual variances *plus* drift uncertainty. Report how much
   of each horizon's variance each part contributes. **Expect the conditional
   variance to matter at h1–h3 and be swamped by drift at h12.** Say so in the
   output rather than presenting a narrower h12 fan as a win.
5. **Tick risk.** RPI all-items is published to 1dp, so one index tick is
   ~2.4bp at current levels, the same size as the 2.9bp residual. For each
   month, output the probability distribution of the clothing contribution in
   ticks, not only in bp.

**Evaluation:** the same 121 rolling origins (Jun15–Jun25), re-estimating
everything at each origin (trap 2). Score interval coverage (50/80/90%), CRPS
and log score for the conditional fan vs the current unconditional fan, by
horizon and by calendar month. It ships only if it beats the unconditional fan
out of sample on CRPS at h1 without worse coverage. If it doesn't, say so and
keep the unconditional fan.

**Outputs:** a committed monthly table (next 12 months × calendar month:
expected contribution, sd, p10/p50/p90, tick probabilities, effective weight)
in the analytics export and the digest.

### Workstream 2: Carli reconstruction from quote-level data

**Goal:** compute the Carli elementary aggregate directly, as the unweighted
mean of price relatives within item strata. Then produce:

- **(a) Price dispersion,** which drives the Carli–Jevons gap and is the wedge
  signal.
- **(b) Real-time sale-timing detection.**

Do it in two stages:

**2a. Historical wedge from ONS public price quotes (no licence needed).**
Using the dataset from Task 0c, rebuild per item and stratum the Carli and
Jevons elementary aggregates against the January base price (trap 4), with
ONS replacement handling (trap 5). Aggregate with stratum weights to the
clothing & footwear section, on both RPI and CPI structures. Outputs:

- A monthly **bottom-up formula-effect series for clothing**, 2010 onward.
  This replaces the dead CRFV.
- **Validation against CRFV over the pre-Feb 2020 overlap,** and against
  published CHBJ and D7BW throughout. This is the answer key. Report MAE of
  the reconstructed section index vs published, by year.
- The non-formula residual (trap 7) and the two-calendar-year decomposition
  (trap 6).
- Dispersion measures per stratum and section: var(log relative), IQR of
  relatives, share of quotes flagged on sale at base vs current.

**2b. Real-time lead from licensed data.**

`[[TO FILL — licensed data specification. The original request cut off at
"LICENSED P". Needed: provider(s), licence terms (storage, derived-value
republication, retention), coverage (retailers, items, geography), frequency
and latency, fields (price, list vs sale flag, product ID stability, size/
colour variants), history depth, and cost.]]`

Whatever the provider turns out to be, the session should:

- **Before building, confirm the licence permits** automated recurring pulls,
  storing quote-level data in BigQuery, and committing *derived aggregates*
  to a git repository. If it does not, stop and tell me.
- Map licensed products to ONS item strata. Record match confidence per
  product, and never force a match.
- Rebuild the same Carli/Jevons/dispersion measures on licensed data for the
  months where 2a has ONS quotes. **Calibrate the licensed-data dispersion to
  ONS-quote dispersion on the overlap** before using it as a forward signal.
- **Sale-timing detection:** daily share of tracked products on sale per
  stratum, and the detected onset of January and summer sales relative to that
  month's index day (2nd or 3rd Tuesday; reuse `onscal.py`). The Jan and Jul
  seasonal drops depend on whether the sale has started by index day.
- **Headline test:** does the dispersion signal beat the **random-walk wedge**
  (h12 MAE 0.71pp) at any horizon on rolling origins? Report h1/h3/h6/h12. The
  random walk is a strong benchmark. If the signal only helps at h1–h3 (likely,
  per trap 6), that is still worth having. Say so plainly rather than
  stretching it to h12.

### Workstream 3

`[[TO FILL — not included in the original request.]]`

### Non-goals

- **Do not try to forecast the 12-month drift with time-series methods.**
  Error-correction and longer drift windows were tested and failed. No
  trailing window explains more than ~10% of next-12m growth variance. Drift
  enters as an uncertainty to carry, not a quantity to model.
- Not scraping retailer websites. Public ONS data and properly licensed data
  only.
- No personal data of any kind.
- **Do not claim a model beats a benchmark before it has done so out of
  sample** on the stated rolling origins, with everything re-estimated at each
  origin.

### How I want you to work

Correct me where this brief is wrong. On the air-fares build, three of my
stated assumptions were wrong on the evidence, and saying so was worth more
than implementing them faithfully. Task 0b already lists three numbers that
look inconsistent, so start there. State the correction, then build.

Tests must run with no network access. Commit messages should explain *why*,
not restate the diff. Ship workstream 1 end-to-end (engine, evaluation,
digest, export) before starting workstream 2.

---

## Notes for you (not part of the prompt)

- **Fill the two `[[TO FILL]]` blocks first.** The request was cut off
  partway through workstream 2. As written, the session can build workstream 1
  and stage 2a, then must stop at 2b and 3.
- **2a may be most of the value.** If the ONS price-quotes file carries
  clothing with sale flags and base prices, it rebuilds a clothing
  formula-effect series historically at no licence cost, and CRFV gives a
  validation window before 2020. Licensed data then only has to supply the
  *lead*, not the measurement.
- **The seasonal-factor table adding up to +7.77pp is the thing I'd check
  first.** If the factors carry drift, every "calendar month contribution" is
  currently drift plus seasonal, and the Jan/Feb/Sep bp numbers shift once
  the drift is taken out.
- **Workstream 1 is where the fan narrows, and only at short horizons.** At
  h12 the drift uncertainty dominates whatever the monthly variances do.
- Same secrets as the siblings: `GCP_PROJECT`, `GCP_WIF_PROVIDER`,
  `GCP_SA_EMAIL`, plus a separate `BQ_DATASET`. Workstream 1 needs no API key.

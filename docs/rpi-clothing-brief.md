# Build brief: RPI Clothing & Footwear Nowcast and Carli-Wedge Measurement

*A starting prompt for a fresh Claude Code project. Paste the whole thing.*

*Sibling of the air-fares (`uk-airfares/`) and hotels (`uk-hotels/`) pipelines
in `elpatcho01/Apps`. Unlike those, this one does not start as a
price-collection problem: workstream 1 needs no new data at all. The "Traps"
section combines mistakes actually made on the sibling builds with ones
specific to a Carli index. Read it before writing code.*

---

## Paste this

---

I want a **nowcasting and wedge-measurement pipeline for RPI Clothing &
Footwear**, ONS series **CHBJ** (Jan 1987 = 100). It has three workstreams in
strict priority order. Each ships end-to-end (engine, backtest, digest line,
export) before the next is started.

There is reference infrastructure at `github.com/elpatcho01/Apps`: `uk-airfares/`
and `uk-hotels/`. **Read `uk-airfares/README.md` first, then
`src/ukairfares/onscal.py`, `index.py`, `validate.py`, `digest.py`,
`export.py` and the workflow files.** Carry over the following:

- the append-only BigQuery schema style;
- Workload Identity Federation auth;
- the committed monthly digest and analytics JSON export;
- the provider-protocol-plus-mock pattern;
- the splice construction in `index.py`;
- the validation discipline: rolling origin, refuse to score on thin overlap,
  and label a variant chosen after seeing the answers as chosen after seeing
  the answers.

Put the project in a new directory `uk-rpi-clothing/`. Tell me where you
deliberately diverge and why.

### What is already established. Do not re-derive it.

| Fact | Value |
|---|---|
| Target | CHBJ, RPI clothing & footwear. Jun 2026 = **325.0**, **+5.6% YoY** |
| RPI weight | **~42 parts per thousand**. Confirm against CZHJ (Task 0) |
| CPI counterpart | D7BW = **118.7**, **−0.5% YoY** |
| Wedge (RPI − CPI YoY) | **5.99pp**, almost entirely the Carli formula effect |
| Scanner-data change, Feb 2026 | Groceries only. Clothing (COICOP division 03) untouched, so its Carli wedge survives intact and its **share** of the residual RPI−CPI formula wedge has risen |
| CRFV (ONS formula-effect series) | **Dead after Jan 2020.** The wedge has to be rebuilt bottom-up |
| Raw MoM sd | 2.21pp; **0.70pp** residual after seasonal |
| Current model MAE, 121 rolling origins Jun15–Jun25 (pp of index level) | h1 **0.90** · h3 1.38 · h6 1.72 · h12 2.74 |
| 12-month drift | **Near-unforecastable.** No trailing window explains more than ~10% of next-12m growth variance. Error-correction and longer drift windows were tested and failed |
| Wedge persistence | Random-walk wedge: **h12 MAE 0.71pp vs 2.98pp** for the leg. A 76% reduction, from 12.5bp to 3.0bp of headline |

Seasonal factors, stated as year-demeaned, 2016–2026 excluding 2020/21 (pp of
MoM):

| Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|
| −3.60 | +4.25 | +2.24 | +0.87 | +0.76 | −0.60 | −2.19 | +1.88 | +3.22 | +0.80 | +0.72 | −0.58 |

**The conversion used throughout:** 1pp of error on the clothing leg ≈ **4.2bp
of headline RPI** (weight 42/1000; this is how 2.98pp → 12.5bp and 0.71pp →
3.0bp were derived). Treat it as approximate; trap 1 explains why it moves
within the year.

### Constraints

- **Method selection by rolling-origin backtest.** Among methods whose MAE is
  within **5%** of the best, pick the **simplest**. Write down the complexity
  ordering of the candidate methods *before* running the backtest, in the
  commit that adds them. Choosing "simplest" afterwards is just another way of
  picking the winner.
- **Exclude 2020–21 from seasonal estimation,** and from variance estimation
  and backtest origins as well (trap 8).
- **ONS fetch uses `cy.ons.gov.uk`.** That mirror serves current full tables
  where `www.ons.gov.uk` is cache-unreliable. Make it the single configured
  base URL, record the URL each vintage came from on every row, and do not
  silently fall back to `www`. If the mirror fails, fail loudly.
- **Every reconstruction is validated against published CHBJ.** No
  reconstructed or nowcast series is reported without its error against
  CHBJ beside it.
- **Do not build speculative analytics tabs.** The export and digest carry
  exactly the outputs listed under each workstream below, and nothing else.
  If you think another view would help, propose it in the PR description; do
  not build it.

### Task 0: confirm what needs confirming, then stop and report

**0a. Series.** Pin every series ID and confirm each one's basis, first
observation and latest value against the table above, fetched from
`cy.ons.gov.uk`: CHBJ, D7BW, RPI all-items (CHAW), and the clothing &
footwear RPI weight. **Confirm the weight against CZHJ** and record it for
every year in the sample, since it changes each January.

**0b. Numbers to reconcile before building on them.** Report each one, and
correct me if it is wrong:

- **The seasonal factors do not add up to zero.** They add up to **+7.77pp**.
  Year-demeaned factors should add up to ≈0 by construction. Either they carry
  the average annual drift (≈7.8%/yr), or the label is wrong. That changes the
  centre of every monthly bp figure in workstream 1, though not their spread.
- **Define the wedge exactly.** Say whether it is the difference of published
  1dp YoY rates, of YoY rates computed from 1dp index levels, or of log
  changes. The rounded published rates give 5.6 − (−0.5) = 6.1pp, not 5.99pp.
  The definitions differ by up to ~0.1pp, which is material against a 0.71pp
  MAE. Pick one and use it everywhere.
- **Units of the MAE table.** Confirm whether "pp of index level" means
  percent of level (which the 4.2bp/pp conversion assumes) or index points.
  At CHBJ ≈ 325, 0.90 index points would be only 0.28%.
- **Leg h12 MAE is quoted as both 2.74pp and 2.98pp.** Establish whether these
  come from different samples or different definitions. The 76% claim uses
  2.98.
- **Index day.** The working rule is "Tuesday nearest the 13th", which always
  falls on the 10th–16th. `onscal.py` treats the index day as the 2nd or 3rd
  Tuesday, confirmed retrospectively from the next CPI bulletin. The
  nearest-13th rule is a strict subset of that. Check the rule against the
  bulletin-confirmed dates for 2016–2026 and report every month where they
  disagree.

**0c. ONS public price quotes.** ONS publish **"Consumer price inflation item
indices and price quotes"** monthly: item-level indices plus the individual
locally collected quotes, with stratum and shop weights. Establish:

- whether clothing & footwear items are in it, from when, and with what
  coverage relative to the full ONS sample;
- the fields actually present. From memory these include a price relative, a
  base price, stratum weight/type, region, shop type, and an indicator box
  flagging sale prices and comparable replacements. **Verify every one;**
- the row count and number of distinct months actually loaded. Count them
  yourself; don't trust the release title.

This file is the **answer key at quote level** for workstreams 2 and 3.

**0d. Vendor due diligence** (see the workstream 2 data spec). Fill in the
vendor matrix and recommend one. **If no vendor meets the hard requirements,
stop and tell me.** Do not build workstreams 2b or 3 against a panel that
cannot support them.

Stop and report after Task 0.

### Traps. Read this section twice.

**1. The fixing contribution is not weight × MoM.** RPI is a
January-linked chain. Within a year, all-items is a weighted sum of section
indices *relative to their January level*. So the section's contribution to
the all-items MoM is:

```
contrib_t = w × (I_c,t − I_c,t−1) / I_c,Jan  ÷  (I_all,t−1 / I_all,Jan)
```

The bp figures in workstream 1 were computed as `w × MoM` (42/1000 × −3.60 =
−15.1bp). Clothing is low in January and rises through the spring and autumn
ranges, so its effective weight drifts by several percent through the year.
Recompute the figures properly, and report the effective weight by month and
how far the naive figures are off.

**2. Full-sample seasonal factors are look-ahead.** The table above was
estimated on 2016–2026. Every rolling-origin evaluation must re-estimate
seasonals, variances and any calibration **using only data available at each
origin.**

**3. Nine observations per calendar month cannot support twelve free
variances.** With n≈9 per month an sd estimate carries roughly ±25% sampling
error. The Feb/Oct variance ratio of ~3.1 on (8, 8) degrees of freedom is
borderline, not settled. Shrink toward the pooled variance.

**4. The Carli relative is against the January base price, not last month.**
The RPI elementary aggregate is the unweighted arithmetic mean of
`p_t / p_base` within each stratum, and the base is reset each January. It is
**not** a chained mean of month-on-month relatives. That is why the
Carli–Jevons gap *accumulates through the year and resets in January*. Carli
− Jevons ≈ ½·var(log relative), and sale bounce is what generates that
variance.

Worked example: a quote on sale in January at 50% off that returns to full
price has relative 2.0. A quote at full price in January that later goes 50%
off has relative 0.5. Carli averages them to **1.25**, Jevons to **1.0**. Make
this a unit test.

**5. Replacements and imputation are most of the difficulty.** Clothing has
extreme product churn: comparable replacements, non-comparable replacements
with quality adjustment, temporarily missing quotes and seasonal items. Each
has specific ONS handling in the CPI/RPI Technical Manual. Getting these wrong
moves dispersion, and dispersion is the signal. Cite the manual section for
each rule, and tag every reconstructed row with the handling that was applied.

**6. The YoY wedge straddles two calendar years.** The YoY wedge at month *m*
is three parts: the part-year wedge accumulated Jan→*m* this year, the
January-link effect, and the part-year wedge *m*→Dec of last year. Report
these separately. A random walk on the YoY wedge mixes them, which is why it
may be beatable at short horizons and not at h12.

**7. "Almost entirely formula effect" should be measured every month.** RPI
and CPI clothing also differ slightly in weights and aggregation structure.
Produce the non-formula residual as its own series; do not absorb it into the
Carli term.

**8. The 2020/21 hole.** Shops were legally closed and ONS imputed rather than
collected. Exclude it from seasonal estimation, variance estimation, backtest
origins and quote-level reconstruction. Establish the exact affected months
from the quotes file rather than assuming. On air fares the hole was nine
specific months, not two whole years.

**9. A vendor panel measures the market, not ONS's sample.** ONS price a
fixed, small set of quotes per item, stratified by region and shop type,
including independents. A vendor panel will not contain those quotes. Part of
the 0.70pp residual is ONS's own sampling noise, which **no** external panel
can predict. Measure that floor (workstream 3, step 1) before promising any h1
number.

**10. Licensing a scraper shifts the ToS risk; it does not remove it.** Some
candidate vendors build their data from retailer websites. "Licensed panel
only" is satisfied only if the vendor warrants lawful provenance and permits
our use. Get that in the contract (0d), not assumed from the sales deck.

**11. Carried over verbatim from the sibling projects.**
- Append-only tables with a `current_*` view; never UPDATE or DELETE.
- Don't split SQL on `;`.
- WIF, not service-account keys.
- BigQuery is reachable only inside a workflow run, so export aggregates to
  committed JSON. Never commit vendor quote-level rows; the licence will
  forbid it anyway.
- The monthly digest must commit (GitHub disables schedules after 60 days
  without a commit) and must never say "healthy" underneath a failed query.
- Compare contexts against literals in bash, not in GitHub expressions.
- ONS may be unreachable from the dev sandbox, so give every fetcher a
  `--dump` mode and test against committed fixtures.

The air-fares README explains why each one exists.

---

### Workstream 1: monthly fixing exposure (no new data)

**Goal:** replace the current unconditional fan with a
**calendar-month-conditional** distribution of clothing's contribution to the
monthly RPI all-items change, in bp.

Starting figures (naive `w × MoM`, to be recomputed per trap 1): Jan −15.1bp,
Feb +17.9bp, Sep +13.5bp, Mar +9.4bp, Jul −9.2bp, Aug +7.9bp. Unconditional
residual sd 2.9bp (4.8bp at p90). Within-month sd ranges from 0.97pp (Feb) to
0.55pp (Oct).

**Method.**

1. **Contribution engine:** the trap 1 formula, with the correct-year weight.
2. **Conditional mean:** seasonal factor plus a trailing-mean drift. Drift is
   not modelled (see non-goals); its uncertainty is carried.
3. **Conditional variance.** Candidates, in pre-registered complexity order:
   - **(i)** unconditional (the incumbent);
   - **(ii)** two regimes: sale-transition months Jan/Feb/Jul/Aug/Sep vs the
     rest;
   - **(iii)** twelve monthly variances shrunk toward the pooled value, with
     the shrinkage chosen out of sample;
   - **(iv)** twelve raw monthly variances.
4. **Fan by horizon.** For h = 1…12, the h-step variance is the sum of
   month-specific residual variances plus drift uncertainty. Report the share
   of each horizon's variance that comes from each part.

**Selection:** the 121 rolling origins, with everything re-estimated per
origin. Score CRPS and 50/80/90% interval coverage by horizon and by calendar
month. Apply the 5%/simplest rule to CRPS. Keep the incumbent (i) unless a
candidate beats it under that rule without worse coverage.

**Outputs (only these):** one table for the next 12 months with expected
contribution, sd, p10/p50/p90 (all in bp), and effective weight. It goes in
the analytics export and gets one section in the digest.

**Expected gain.** **Zero change to point MAE.** The conditional mean is the
same seasonal-plus-drift, so h1 stays at 0.90pp (3.8bp). The gain is
**calibration**: the fan becomes the right width in each month instead of the
average width in every month. Before shrinkage, the 90% half-width for the
monthly headline contribution moves as follows:

| | sd (pp) | 90% half-width (bp of headline) |
|---|---|---|
| Unconditional | 0.70 | ±4.8 |
| Feb | 0.97 | **±6.7**, currently understated by ~2bp |
| Oct | 0.55 | **±3.8**, currently overstated by ~1bp |

Shrinkage will pull these toward ±4.8. Report the shrunk values; they are
the real claim.

**Does not improve:** point forecasts at any horizon, the h12 fan (drift
variance dominates it), or the 12-month drift.

---

### Workstream 2: Carli reconstruction from quote-level data

**Goal:** compute the Carli elementary aggregate directly, as the unweighted
mean of price relatives within item strata. Then produce:

- **(a) price dispersion,** which drives the Carli–Jevons gap and is the wedge
  signal;
- **(b) real-time sale-timing detection.**

It runs in two stages. The first needs no vendor.

#### 2a. Methodology, built and validated on ONS public quotes

For each item and stratum, per month:

1. Quote relative `r = p_t / p_base`, with the base reset each January (trap
   4). Apply replacement and imputation rules per the Technical Manual (trap
   5).
2. **Carli** elementary aggregate = mean(r) within stratum. **Jevons** =
   geometric mean(r), computed alongside it on the same quotes.
3. Aggregate the strata with ONS stratum weights, then items with item weights,
   to the section. Build it on the RPI structure (Carli) and the CPI
   structure (Jevons).
4. Chain at January to produce a reconstructed CHBJ and a reconstructed D7BW.
5. **Formula effect** = RPI-structure minus CPI-structure computed on
   identical quotes. Non-formula residual = observed wedge minus formula
   effect (trap 7). Report the three-part decomposition from trap 6.
6. **Dispersion:** var(log r) and IQR of r per stratum and for the section;
   share of quotes flagged on sale at base vs current.

**Validation:**
- Reconstructed CHBJ vs published CHBJ: MAE by year, with max error. The
  target is sub-0.1pp; anything larger means a handling rule is wrong, so find
  it before going on.
- Reconstructed D7BW vs published D7BW, the same way.
- Reconstructed formula effect vs **CRFV** over the pre-Feb 2020 overlap. This
  is the only external check on the wedge itself.

**Outputs (only these):** monthly formula-effect series (replacing CRFV),
non-formula residual, section dispersion, and the validation table.

#### 2b. Licensed panel: data spec and vendor requirements

**Licensed panel only. No scraping.** Candidate vendors: **NielsenIQ,
Circana, EDITED, Retviews.**

**Hard requirements.** A vendor failing any of these is out.

| Requirement | Detail |
|---|---|
| Granularity | **SKU-level** price per fascia. Size/colour variants either rolled to a stable parent SKU or identified so we can roll them |
| Markdown flag | Per SKU per observation: **full price, current price, and a markdown/promo flag** (depth derivable). Without it, sale timing is inferable only from price drops, which confuses markdowns with permanent reprices |
| Frequency | **Weekly minimum,** with weeks defined so we can isolate the week containing index day. Daily or price-on-date preferred (index day is a single Tuesday, and sales often start midweek) |
| Latency | Data for the index-day week **delivered within ~7 days.** Publication is ~5 weeks after index day, so later delivery erodes the lead |
| Coverage | **Major UK fascias** covering the bulk of UK clothing spend: mass market, department stores, value, sportswear, footwear specialists, and online pure-players |
| Basket shape | Sample mappable to the **ONS clothing item list** (men's / women's / children's garments, footwear, accessories). The vendor supplies a category taxonomy we can map to ONS items |
| History | **≥ 5 years**, ideally back to 2016, so the backtest can cover a meaningful share of the 121 origins. Less history means fewer origins; say how many |
| Stable identity | Persistent SKU IDs across weeks, with discontinuation visible. Churn handling depends on it |
| Licence | Permits automated recurring delivery, storage in our BigQuery, derived aggregates committed to a git repo and used in trading/research output, and retention after the contract ends for historical backtests. **Warranted lawful provenance** (trap 10) |

**What to find out per vendor** (report as a matrix):
- Data origin: POS/transaction, retailer-supplied, or web-collected.
- Fascia list and estimated UK clothing spend share.
- Whether **value/offline-only retailers are covered.** Value clothing is a
  large share of UK spend, and a web-collected panel will miss any fascia
  without full online sales.
- Markdown flag definition, frequency, latency and history.
- Cost and minimum term.

Do not assume the answers from the vendor's general reputation. POS vendors
are strongest in grocery and may be thin in UK apparel. Web-collected vendors
have breadth but the provenance question from trap 10.

**Build (only once a vendor is signed and delivering):**
- A `PanelProvider` protocol with a vendor adapter and a mock. A guard
  prevents mock data reaching real tables, as in the siblings.
- Map SKUs to ONS items with a confidence score per mapping; never force a
  match. Report the share of ONS item weight covered.
- Recompute the 2a measures on panel data: Carli, Jevons, dispersion, sale
  share.
- **Calibrate** panel dispersion and panel relatives to ONS-quote measures
  over the overlap months, re-estimated per origin.
- **Sale-timing detection:** weekly (or daily) share of SKUs marked down, per
  item group. Report detected onset of the January and summer sales
  relative to that month's index day, flagging when onset falls in the week
  of index day.

**Wedge forecast.** Candidates, in pre-registered order:
- **(i)** random-walk wedge (the incumbent);
- **(ii)** random walk plus a dispersion-implied adjustment to the current
  year's part-year component (trap 6);
- **(iii)** a regression of the wedge change on dispersion change and sale
  share.

Backtest at h1/h3/h6/h12 and apply the 5%/simplest rule to MAE. First
establish the random-walk MAE at h1/h3/h6; only h12 (0.71pp) is known.

**Expected gain.**
- **2a** is measurement, not forecast accuracy. It restores a monthly
  formula-effect series that has been missing since Feb 2020, and lets each
  month's wedge be attributed rather than assumed.
- **2b's forecast gain is bounded and probably small.** The random-walk wedge
  already has h12 MAE of 0.71pp (**3.0bp of headline**). That is the ceiling
  on what any wedge improvement can recover at h12, and a realistic gain is a
  fraction of it. Any gain is most likely at h1–h3, where the current-year
  dispersion is observable. If no candidate clears the 5% rule, the random
  walk stays, and say so.
- **2b's other job is to feed workstream 3.** Sale timing and panel relatives
  are the inputs to the index-day nowcast, and most of the value comes from
  there.

**Does not improve:** the 12-month drift; the CPI leg of the wedge route; any
month before panel history begins.

---

### Workstream 3: index-day nowcast

ONS collect on **index day (Tuesday nearest the 13th)**, about **5 weeks
before publication**. With panel data covering index day, h=1 becomes
measurement rather than forecasting.

**Target:** h1 MAE **below 0.30pp**, from 0.90pp.

**Architecture.**

```
cy.ons.gov.uk ──► onsfetch ──► ons_published (CHBJ, D7BW, CHAW, weights)
                     └───────► ons_price_quotes ──► carli (2a) ──┐
vendor ──► panel adapter ──► panel_observations ──► carli (2b) ──┤
                                                                ▼
                                       nowcast: index-day week, calibrated
                                       relative, spliced onto last published
                                       CHBJ ──► nowcasts (append-only, vintaged)
                                                                │
                                   validate vs published CHBJ ◄─┘ (on release)
                                                                │
                                             digest + export ◄──┘
```

1. **Measure the floor first.** Bootstrap ONS's own quote sample from the
   public price-quotes file (resample quotes within strata) and compute the sd
   of the resulting section MoM. That sd is noise no external panel can
   predict (trap 9). **If the implied MAE floor is at or above 0.30pp, the
   target is infeasible. Report the achievable target instead and stop for
   confirmation.**
2. **Identify index day** with `onscal.py` (nearest-13th rule as the
   prediction, confirmed retrospectively from the bulletin), and select the
   panel week containing it.
3. **Panel relative:** calibrated Carli relative for the section, index day
   *m* vs index day *m*−1, using the 2b mapping and calibration.
4. **Splice:** `nowcast CHBJ(m) = published CHBJ(m−1) × calibrated panel
   relative`, the same construction as `uk-airfares/src/ukairfares/index.py`.
   Take the level from ONS; the panel contributes only the change. In
   January, splice across the chain-link using the January conventions from
   2a.
5. **YoY and headline contribution.** The YoY base CHBJ(m−12) is already
   published, so the h1 annual rate follows directly from the nowcast level.
   Its error equals the level error. Convert both to bp of headline with the
   trap 1 effective weight.
6. Write the nowcast as an append-only vintaged row with the panel week, the
   index-day source (predicted or confirmed), the calibration vintage, and
   the variant.

Candidate nowcast methods, in pre-registered order:
- **(i)** the current seasonal model (incumbent, 0.90pp);
- **(ii)** raw panel relative;
- **(iii)** calibrated panel relative;
- **(iv)** calibrated relative plus sale-onset adjustment for Jan/Jul.

Apply the 5%/simplest rule on rolling origins restricted to months with panel
coverage.

**Outputs (only these):** each month's nowcast (level, MoM, YoY, headline
contribution in bp, and interval) in the export and digest, and the
validation row once CHBJ publishes.

**Expected gain.** h1 **0.90pp → <0.30pp**, which is **3.8bp → <1.3bp of
headline RPI**: about **2.5bp** better on the headline monthly and annual
prints, subject to the floor from step 1. This is the largest gain in the
brief. It is also the one most exposed to trap 9.

**Does not improve:** h2 and beyond. Once month *m* is published, the nowcast
advantage is gone. A better jump-off improves h12 only marginally: removing
the h1 error in quadrature gives ≈2.74 → ≈2.6pp, not worth claiming. h12 is
the wedge route's job. It does not improve the 12-month drift at all.

---

### Validation plan against published CHBJ

| Check | Against | When | Pass bar |
|---|---|---|---|
| 2a reconstruction | Published CHBJ and D7BW, every month 2010→ | Once, then monthly | MAE < 0.1pp; every month > 0.2pp explained |
| 2a formula effect | CRFV, up to Jan 2020 | Once | Reported; large gaps explained by handling rule |
| 2b panel relatives | 2a ONS-quote relatives, overlap months | Per origin | Calibration error reported by item group |
| WS1 fan | Realised contributions, 121 origins | Backtest, then monthly | Beats unconditional on CRPS under 5%/simplest rule; coverage no worse |
| WS2 wedge | Realised wedge, h1/h3/h6/h12 | Backtest, then monthly | Beats random walk under 5%/simplest rule, or random walk stays |
| WS3 nowcast | Published CHBJ on release day | Every month | Backtest MAE < 0.30pp *and* above the bootstrap floor |

Verdicts follow the siblings: `INSUFFICIENT_DATA` → `PROVISIONAL` → `SCORED`.
Live WS3 scoring needs three consecutive *published, collected* months
before it is `SCORED`. Backtest numbers are reported separately from live
numbers and never merged.

---

### Expected accuracy gain: summary

| Workstream | What improves | Before | After (target) | Headline RPI impact |
|---|---|---|---|---|
| 1. Fixing exposure | Fan calibration by month | Uniform ±4.8bp (90%) | Feb ≈ ±6.7bp, Oct ≈ ±3.8bp (pre-shrinkage) | **No point gain.** Correctly sized risk: Feb ~2bp wider, Oct ~1bp narrower |
| 2. Carli reconstruction | Wedge measurement; possibly h1–h3 wedge | RW wedge h12 0.71pp (3.0bp); no formula series since 2020 | Formula series restored; wedge gain only if > 5% | **≤ 3.0bp ceiling at h12**, realistically a fraction. Main value is measurement and feeding WS3 |
| 3. Index-day nowcast | h1 level and YoY | 0.90pp (3.8bp) | < 0.30pp (< 1.3bp) | **~2.5bp** on the monthly print, subject to the sampling floor |

**None of this touches the 12-month drift.** The wedge route (CPI leg plus
random-walk wedge) already handles h12 at 3.0bp of headline. Nothing here is
aimed at it, and no result here should be presented as improving it.

### Non-goals

- **Do not try to forecast the 12-month drift with time-series methods.**
  Error-correction and longer drift windows were tested and failed.
- No scraping, by us or through a vendor that cannot warrant provenance.
- No speculative analytics tabs, dashboards or views beyond the outputs listed
  per workstream.
- Never commit vendor quote-level data to git.
- No personal data of any kind.
- **Do not claim a gain before it has been shown out of sample** on rolling
  origins, with everything re-estimated per origin.

### How I want you to work

Correct me where this brief is wrong. On the air-fares build, three of my
stated assumptions were wrong on the evidence, and saying so was worth more
than implementing them faithfully. Task 0b already lists numbers that look
inconsistent, so start there. State the correction, then build.

Tests must run with no network access. Commit messages should explain *why*,
not restate the diff. Order of work: Task 0 → WS1 → 2a → vendor decision → 2b
→ WS3.

---

## Notes for you (not part of the prompt)

- **2a may be most of the value per pound.** If the ONS price-quotes file
  carries clothing with sale flags and base prices, it rebuilds the formula
  effect historically at no licence cost and validates against CRFV before
  2020. It also measures the sampling floor that decides whether WS3's 0.30pp
  target is achievable, before any vendor money is spent.
- **Check the seasonal table first.** If the factors add up to +7.77pp because
  they carry drift, every "calendar month contribution" is currently drift
  plus seasonal.
- **The WS3 target is the claim most at risk.** Part of the 0.70pp residual is
  ONS's own quote sampling, which a vendor panel cannot see. The bootstrap in
  WS3 step 1 is there to find that out cheaply.
- **Vendor fit is uncertain in both directions.** POS vendors may be thin in
  UK apparel. Web-collected vendors raise the provenance question and miss
  offline-only value retailers. The Task 0d matrix should settle it.
- **Deliberately left out** under the no-speculative-tabs rule: probability of
  each 0.1-point rounding outcome of the published RPI, and per-stratum
  dispersion views. Either is easy to add later if you want it.
- Same secrets as the siblings: `GCP_PROJECT`, `GCP_WIF_PROVIDER`,
  `GCP_SA_EMAIL`, plus a separate `BQ_DATASET`. Workstream 1 needs no API key.

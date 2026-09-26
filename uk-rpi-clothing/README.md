# RPI Clothing & Footwear: fixing exposure, Carli wedge, index-day nowcast

Nowcasting and wedge measurement for ONS **CHBJ** (RPI clothing & footwear,
Jan 1987 = 100), built from `docs/rpi-clothing-brief.md`. There are three
workstreams: monthly fixing exposure (WS1), Carli reconstruction from quote
data (WS2), and an index-day nowcast (WS3).

---

## Read this first: what this can and cannot currently tell you

**1. The brief's bp figures used a weight that is eleven years old.** The RPI
clothing & footwear weight (CZHJ) is **23 parts per thousand in 2026**, not ~42.
It has fallen every year: 41 (2016), 37 (2020), 29 (2022), 25 (2025), 23 (2026).
42 was the 2015 figure. Every headline-bp number in the brief is about **1.8×
too large** for 2026. One pp on the clothing leg is now about **2.4bp** of
headline RPI, not 4.2bp. Re-stated:

| Brief said | At the 2026 weight |
|---|---|
| Jan −15.1bp, Feb +17.9bp, Sep +13.5bp | Jan ≈ −9bp, Feb ≈ +10bp, Sep ≈ +7bp (exposure table below) |
| Residual sd 2.9bp | ≈ 1.8bp |
| Random-walk wedge h12: 12.5bp → 3.0bp | ≈ 7.2bp → 1.7bp |
| WS3 target: 3.8bp → 1.3bp | ≈ 2.1bp → 0.7bp |

**2. There is no licensed panel yet, so WS3 is built but not live.** The
vendor interface, mock, SKU mapping, calibration, sale-onset detection and
index-day splice are implemented and tested on a deterministic mock. No real
nowcast can be produced or scored until a vendor delivers. The monthly digest
says so every month.

**3. ONS is only reachable from GitHub Actions.** ons.gov.uk (including the
cy.ons.gov.uk mirror) is blocked from the development sandbox. All real-data
results below come from the push-triggered `rpiclothing-probe.yml` research
runs, read back from their job logs.

**4. BigQuery for this project is not yet configured.** The monthly workflow
runs in `DRY_RUN` mode (it computes, and commits the digest and export)
until `GCP_WIF_PROVIDER` is set and a dataset variable
(`RPICLOTHING_BQ_DATASET`, default `rpi_clothing`) exists. It reuses the
siblings' WIF setup.

---

## What Task 0 established on real data

Every item in brief Task 0b was checked against live ONS data (research runs
1–3, 2026-09-26):

| Question | Answer |
|---|---|
| Weight (CZHJ) | **23ppt in 2026.** CZHJ is annual-only, which is why a monthly fetch returned nothing. Sub-section weights: CZXY/CZXZ/CZYA/CZYB/CZYC |
| Seasonal table: "year-demeaned"? | **No, it is the raw mean MoM** (Feb 2016–Jun 2026, excluding 2020/21; max difference 0.10pp). It sums to +8.0pp because it carries the average drift. Year-demeaned factors sum to zero and are about 0.67pp lower each month |
| Raw / residual MoM sd | 2.24 / 0.71pp (brief: 2.21 / 0.70) ✓ |
| Wedge definition | **12-month log difference:** 100·[ln(CHBJ_t/CHBJ_t−12) − ln(D7BW_t/D7BW_t−12)] gives 5.993pp for Jun 2026. Differences of rates give 6.14 (1dp levels) or 6.2 (rounded rates) |
| Incumbent model | Seasonal factors from the last 10 years plus 12-month trailing-mean drift reproduces h1 0.87 / h3 1.31 / h6 1.80 / h12 **2.97**. That matches the brief's 2.98 leg figure, not the 2.74 in its MAE table |
| Contribution formula (trap 1) | The exact chain-linked formula reproduces ONS's published contribution **CZFY** with MAE **0.27bp**, which is the rounding floor (CZFY is published to 1bp). The naive weight × MoM misses by up to 2.6bp. January is measured on the previous year's chain and weights |
| CRFV | **All-items only.** There was never a clothing-specific formula-effect series to validate against. DRA9 (all-items CPI–RPI formula gap) is still published |
| Price quotes | Published monthly from 2010. Clothing is item prefix **51**, whose sub-codes are exactly the RPI sub-sections (5101 men's, 5102 women's, 5103 children's, 5104 other clothing, 5105 footwear = DOCK–DOCO). From 2026 ONS also publish **RPI_INDEX and RPI_WEIGHT per consumption segment**, an item-level answer key |

---

## Workstream 1: fixing exposure

`fixing.py`, `ws1.py`. The point forecast is the incumbent seasonal+drift
model, unchanged, so WS1 **cannot change point MAE**. What it replaces is the
fan.

Candidates, in pre-registered complexity order: (i) unconditional, (ii)
two-regime, (iii) twelve monthly variances log-shrunk by empirical Bayes,
(iv) twelve raw variances. Everything is re-estimated at each of the 121
origins.

**Result: calendar-month conditioning does not help.** All four are within
0.2% on h1 CRPS, so the 5%/simplest rule keeps (i). February has the worst
CRPS under *every* candidate. Its errors are in the **mean** (the seasonal
jump moves year to year with sale timing), not the variance. A variance
model cannot fix that; WS3 can. The unit tests show why shrinkage collapses:
at n≈9 per month, a Feb/Oct variance ratio of ~3 with the other ten months
flat is within sampling noise.

**The live finding is miscalibration, and it is post hoc.** Every
residual-based fan under-covers at h1: the nominal 90% band holds 76–79% of
outcomes. In-sample residuals understate out-of-sample error, and the
tails are fat (50% coverage is about right, 90% is not). After seeing that,
three candidates were added last in the order and are labelled as chosen
after seeing results:

| Candidate | h1 CRPS | h1 50/80/90% coverage |
|---|---|---|
| unconditional (incumbent) | 0.654 | 44 / 71 / 79% |
| empirical (normal, from past out-of-sample errors) | 0.647 | 47 / 74 / 83% |
| empirical_month | 0.647 | 50 / 73 / 84% |
| empirical_quantile (non-parametric) | *run 4* | *run 4* |

Under the 5%/simplest rule the incumbent stays, because better calibration
barely moves CRPS. A calibration-first rule is reported beside it. **Which
rule governs is an open decision** (see "Decisions needed").

---

## Workstream 2: Carli reconstruction

`quotes.py`, `recon.py`, `ws2a.py`, `carli.py`, `panel.py`.

**2a (no licence).** A loader handles all three quote schema eras. The
reconstruction rebuilds item, sub-section and section indices under six
elementary-aggregate variants (what INDEX_ALGORITHM_RPI 1/2 mean, and shop
weights on or off). Each is validated against CHBJ, DOCK–DOCO and the 2026
per-segment RPI_INDEX. It produces the chained Carli-minus-Jevons formula
wedge on identical quotes and weights, and a quote-resampling bootstrap of
the MoM, which is the WS3 sampling floor.

*Results: research run 4, filled in below when it lands.*

**2b (licensed panel).** `PanelProvider` protocol, deterministic `MockPanel`,
a guard against mock data reaching production, SKU→segment mapping with
confidence (never forced), matched-SKU relatives, per-origin calibration
that refuses thin overlap, and sale-onset detection from markdown share.

**Wedge.** The random walk (incumbent) and a seasonal random walk (average
calendar-month change in the wedge, which needs no panel) are scored on the
121 origins in `wedge.py`.

---

## Workstream 3: index-day nowcast

`nowcast.py`. The index day is predicted as the Tuesday nearest the 13th,
tested to always be a 2nd or 3rd Tuesday. The splice is `CHBJ(m−1) × (1 +
calibrated panel MoM)`. YoY and the headline contribution use the exact
effective weight. Every row records whether the index day was predicted or
confirmed. It is not live until a vendor is signed.

---

## Running

```bash
pip install -r requirements-dev.txt
PYTHONPATH=src python -m pytest                      # no network needed
DRY_RUN=1 PYTHONPATH=src python -m rpiclothing.monthly --month 2026-08   # needs ONS access
```

| Workflow | When | What |
|---|---|---|
| `rpiclothing-probe.yml` | push to `claude/rpi-clothing-**`, dispatch | Research runs on live ONS data (Task 0, WS1 backtest, wedge, WS2a) |
| `rpiclothing-monthly.yml` | 22nd, 08:17 UTC | Exposure table + wedge → BigQuery; digest and export **committed** |
| `rpiclothing-ci.yml` | push/PR, weekly | Tests, actionlint, unpinned canary |

## Invariants

Carried from the siblings:

- Tables are append-only, and analysis reads the `current_*` views.
- SQL is never split on `;`.
- Auth is WIF, never keys.
- Only aggregates are exported to git; vendor SKU data never is.
- The digest always commits, and never says "nothing flagged" above a failed
  section.
- Workflow gates live in bash, not in expressions compared against boolean
  literals.

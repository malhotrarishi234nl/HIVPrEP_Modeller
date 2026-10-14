# PrEP policy suite design

This suite separates structural calibration from economic policy analysis.
The 2008-versus-2010 on-ART knot suite runs first. After review, the selected
2008 calibrated candidate becomes the primary economic backbone. The selected
2010 candidate is used only for a small bridge analysis.

## Locked policy definitions

- Oral comparator: hold 13,000 users from 2025 onward.
- Matched oral expansions: reach 13,500, 14,500, or 16,000 by 2030.
- Wang benchmark: reach 28,000 by 2030. Treat this as a benchmark, not a forecast.
- LENA substitution: replace 10%, 25%, 50%, 75%, or 100% of eligible
  high-activity oral PrEP users. The percentage denominator is not all 13,000
  oral users. Total PrEP stock is intended to remain close to 13,000.
- LENA add-on: retain the 13,000 oral target and add 500, 1,500, or 3,000 LENA
  users by 2030.

The matched oral and LENA add-on scenarios allow like-for-like comparisons at
the same added coverage. Substitution and add-on remain separate deployment
families and must not be plotted as one continuous axis.

## Persistence sensitivity

The main LENA cases use 5% annual discontinuation. The 50% substitution and
+1,500 add-on cases additionally use 10%, 20%, and 30% annual discontinuation.
These are scenario assumptions. The suite does not describe them as literature
estimates.

The model converts annual probability `p` to a quarterly transition probability
as `1 - (1 - p)^(1/4)`. The exact quarterly values are 1.2741%, 2.5996%,
5.4258%, and 8.5309%. Dividing the annual values by four is not permitted.

## Run scope and budget

The primary 2008 analysis contains 19 pairwise scenarios. With 15 common random
number seeds, it requires at most 585 trajectories under the current runner
design: 38 forward arm trajectories and one historical pre-freeze trajectory
per seed.

The 2010 bridge contains five pairwise scenarios: the no-PrEP attribution
context, matched oral +1,500, Wang, 50% substitution, and LENA +1,500. It
requires at most 165 trajectories. The combined economic maximum is 750.
This is separate from the 20,892-trajectory calibration maximum.

## Reporting profile

The workbook remains one package rather than one worksheet per scenario:

1. Executive summary
2. Scenario definitions
3. Oral benchmarks
4. Main LENA results
5. Persistence sensitivity
6. 2008 vs 2010 bridge
7. Seed-level audit
8. Checks and provenance

Figures are independently reproducible after the run: PrEP coverage over time,
matched oral versus LENA expansion, substitution versus add-on, persistence
sensitivity, and paired 2008-minus-2010 differences.

## Launch gate

Preparation and validation run zero simulations. The economic suite remains
blocked until the calibration suite completes, the selected candidates are
bound into the economic plan, and the user explicitly approves the 750-trajectory
economic run.

The preparer writes separate primary-2008 and bridge-2010 runtime configs. The
bridge is materialized as exactly five scenarios, so it cannot silently expand
to the full substitution or add-on grids at launch time.

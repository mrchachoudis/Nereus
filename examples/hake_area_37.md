# Fisheries Research Report - run `20261026T224140-98598a`

**Question:** Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2020

*Generated: 2026-10-26T22:41:41.200602+00:00*

**Scope:** Merluccius merluccius | areas 37.2 | 2010–2020

## Figures

![Landings](figures/fig-landings.png)

*Figure fig-landings: Landings over time with Theil-Sen trend and 95% CI. Source: landings connectors.*

![Spawning stock biomass](figures/fig-ssb.png)

*Figure fig-ssb: Spawning stock biomass over time with Theil-Sen trend and 95% CI. Source: assessment connectors.*

![Kobe plot](figures/fig-kobe.png)

*Figure fig-kobe: Stock-status trajectory in Kobe space (B/B_MSY vs F/F_MSY); lower-right is healthy. Source: assessment connectors.*

![SSB vs SST](figures/fig-covariate-overlay.png)

*Figure fig-covariate-overlay: SSB overlaid with SST (degree_C). Association is not causal. Source: assessment + ocean connectors.*

## Summary

This report addresses: Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2020. It synthesizes 7 quantitative results and 2 references. All numeric claims are grounded in the cited source records.

## Methods

Trends were tested with the Mann-Kendall test and Theil-Sen slope; stock status was classified against MSY reference points (Kobe quadrants); covariate associations used lagged cross-correlation of linearly detrended series, with p-values based on an autocorrelation-adjusted effective sample size (Pyper & Peterman 1998) and Bonferroni-adjusted over the lags tested. All statistics are computed by deterministic, unit-tested functions; associations are not causal.

## Results

Landings show a decreasing trend (Mann-Kendall p=2.62e-05; Theil-Sen slope -495.9 tonnes/yr, 95% CI [-544.4, -455.7]). [A1]
Spawning stock biomass shows a decreasing trend (Mann-Kendall p=5.16e-05; Theil-Sen slope -959.8/yr). [A2]
As of 2020, Merluccius merluccius in 37.2 is classified 'both': biomass below B_MSY and F above F_MSY. [A3]

## Environmental drivers

No significant association between SST and landings after detrending; the best-supported lag is with SST lagging landings by 4 yr (detrended r=0.44, Bonferroni-adjusted p=1). Associational only - not causal. Both series linearly detrended before correlating (raw r=-0.83). Lag with the lowest p selected post hoc over 9 lags; reported p uses an autocorrelation-adjusted effective sample size (N*=5.7 of n=7) and is Bonferroni-adjusted (×9). [A4]
No significant association between SST and SSB after detrending; the best-supported lag is with SST leading SSB by 3 yr (detrended r=-0.28, Bonferroni-adjusted p=1). Associational only - not causal. Both series linearly detrended before correlating (raw r=-0.88). Lag with the lowest p selected post hoc over 9 lags; reported p uses an autocorrelation-adjusted effective sample size (N*=6.7 of n=8) and is Bonferroni-adjusted (×9). [A5]
No significant association between CHLOR_A and landings after detrending; the best-supported lag is with CHLOR_A lagging landings by 4 yr (detrended r=-0.48, Bonferroni-adjusted p=1). Associational only - not causal. Both series linearly detrended before correlating (raw r=0.56). Lag with the lowest p selected post hoc over 9 lags; reported p uses an autocorrelation-adjusted effective sample size (N*=7.0 of n=7) and is Bonferroni-adjusted (×9). [A6]
No significant association between CHLOR_A and SSB after detrending; the best-supported lag is with CHLOR_A leading SSB by 4 yr (detrended r=-0.54, Bonferroni-adjusted p=1). Associational only - not causal. Both series linearly detrended before correlating (raw r=0.55). Lag with the lowest p selected post hoc over 9 lags; reported p uses an autocorrelation-adjusted effective sample size (N*=7.0 of n=7) and is Bonferroni-adjusted (×9). [A7]

## Literature

The literature includes "Stock status and warming-linked distribution shifts of European hake in the central Mediterranean" (2019), relevant to these findings. [L1]
The literature includes "Mediterranean sea surface temperature trends 2000–2020 and implications for demersal fisheries" (2021), relevant to these findings. [L2]

## Limitations

Landings trend in 37.2: only 11 years, so Mann-Kendall has low power to detect a trend. [A1]
SSB trend in 37.2: only 11 years, so Mann-Kendall has low power to detect a trend. [A2]
Stock status in 37.2 is based on the terminal assessment year (2020) only, with no uncertainty on B/B_MSY or F/F_MSY. [A3]
SST vs landings in 37.2: the raw correlation (r=-0.83) reverses sign after detrending (r=0.44), so it reflects shared trends, not year-to-year co-variation. [A4]
SST vs landings in 37.2: only 7 aligned years, and the lag was picked after the fact, so the test has low power and the best lag is unstable. [A4]
SST vs SSB in 37.2: the raw correlation (r=-0.88) is not significant after detrending (adjusted p=1), so it mostly reflects shared trends. [A5]
SST vs SSB in 37.2: only 8 aligned years, and the lag was picked after the fact, so the test has low power and the best lag is unstable. [A5]
CHLOR_A vs landings in 37.2: the raw correlation (r=0.56) reverses sign after detrending (r=-0.48), so it reflects shared trends, not year-to-year co-variation. [A6]
CHLOR_A vs landings in 37.2: only 7 aligned years, and the lag was picked after the fact, so the test has low power and the best lag is unstable. [A6]
CHLOR_A vs SSB in 37.2: the raw correlation (r=0.55) reverses sign after detrending (r=-0.54), so it reflects shared trends, not year-to-year co-variation. [A7]
CHLOR_A vs SSB in 37.2: only 7 aligned years, and the lag was picked after the fact, so the test has low power and the best lag is unstable. [A7]

## Citations

| Marker | Kind | Reference ID | Detail |
|---|---|---|---|
| [A1] | analysis | `trend-landings-merluccius-merluccius-37.2` | Landings of Merluccius merluccius in 37.2 (2010-2020) |
| [A2] | analysis | `trend-ssb-merluccius-merluccius-37.2` | SSB of Merluccius merluccius in 37.2 (2010-2020) |
| [A3] | analysis | `status-merluccius-merluccius-37.2` | Stock status of Merluccius merluccius in 37.2 (2020) |
| [A4] | analysis | `assoc-sst-landings-37.2` | SST vs landings in 37.2 |
| [A5] | analysis | `assoc-sst-SSB-37.2` | SST vs SSB in 37.2 |
| [A6] | analysis | `assoc-chlor_a-landings-37.2` | CHLOR_A vs landings in 37.2 |
| [A7] | analysis | `assoc-chlor_a-SSB-37.2` | CHLOR_A vs SSB in 37.2 |
| [L1] | reference | `sample_lit-1` | Stock status and warming-linked distribution shifts of European hake in the central Mediterranean |
| [L2] | reference | `sample_lit-2` | Mediterranean sea surface temperature trends 2000–2020 and implications for demersal fisheries |

## Data Sources

| Source | Query/URL | Retrieved | # records |
|---|---|---|---|
| sample_landings | sample_landings | 2026-10-26 | 11 |
| sample_assessment | sample_assessment | 2026-10-26 | 11 |
| sample_ocean | sample_ocean | 2026-10-26 | 2 |
| sample_literature | sample_literature | 2026-10-26 | 2 |

---

_Generated by Fisheries Research Agents. Every quantitative claim is grounded in a cited source record; see `report.json` for the full machine-readable audit trail._

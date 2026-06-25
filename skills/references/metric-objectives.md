# Metric Objectives

CogAlpha skills should target a balanced predictive profile. A single strong
metric is diagnostic evidence, not automatic promotion.

## Core Metrics

- `IC`: Pearson information coefficient. Measures linear association between
  factor values and future returns on the validation split.
- `RankIC`: Spearman rank information coefficient. Measures rank-order
  predictive stability and is often the main bottleneck for deployable signals.
- `ICIR`: Information coefficient information ratio. Measures consistency of IC
  over time.
- `RankICIR`: RankIC information ratio. Measures stability of rank predictive
  power over time.
- `MI`: Mutual information. Measures nonlinear dependence that may not appear in
  IC or RankIC.

## Qualified Minima

A candidate is qualified only when it clears the runtime `qualified_thresholds`
for all five metrics: IC, RankIC, ICIR, RankICIR, and MI. Skills should prefer
small robust improvements across the full gate over single-metric spikes.

## Elite Minima

An elite candidate must clear the stricter runtime `elite_thresholds` across IC,
RankIC, ICIR, RankICIR, and MI. Elite status is evidence for future skill
utility, but promotion still requires trace review and rollback metadata.

## Tradeoff Warnings

- Strong IC with weak RankIC can indicate amplitude sensitivity without stable
  cross-sectional ordering.
- Strong RankIC with weak MI can indicate monotonic ranking that lacks useful
  nonlinear information.
- High MI with poor IC or RankIC can be a noisy nonlinear artifact.
- High ICIR or RankICIR without adequate raw IC or RankIC is not enough.
- Any metric gain obtained through leakage, future data, or target labels is
  invalid regardless of score.

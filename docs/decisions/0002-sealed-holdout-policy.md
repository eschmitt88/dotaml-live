# 0002 — Walk-forward sealed holdout for the drifting live data

- Status: accepted
- Date: 2026-06-04

## Context

Dota is **non-stationary**: balance patches move the data distribution. The
research-repo HCE rule (`~/claude-system/claude/rules/evaluation.md`) assumes a
single frozen holdout, which is correct for a stationary problem but fails here:

1. **Staleness.** A frozen test window from an old patch stops representing what we
   serve. A model genuinely better on the live meta can score *worse* on it — the
   signal becomes misleading.
2. **Wasted data.** Freezing a window forever means never training on it, conflicting
   with the goal of using all available data.

We also want to weight recent matches more heavily than old ones.

## Decision

A **walk-forward** holdout with a **head-to-head** promotion gate and a small frozen
**anchor** tripwire, plus **recency-weighted training**:

- **Walk-forward fresh holdout = promotion signal.** Each retrain cycle seals the
  *most recent* window as test (closest to what we serve) and the block before it as
  val; everything older is train. The sealed window is untouched during that cycle's
  search, preserving the anti-overfit guarantee *within* the cycle. Next cycle,
  today's test rolls into train — all data is eventually used; the holdout never
  goes stale. Widths/embargo in `splits.yaml`.
- **Head-to-head gate, not a fixed bar.** Cross-cycle comparability is restored where
  it matters: both the incumbent live model and the candidate are scored on the
  **same** freshly-sealed window; promote only if the candidate beats the incumbent
  (within a margin), passes the probe thresholds, and does not regress on the frozen
  anchor beyond tolerance. The historical `0.6471` v4 anchor is a reference, not a gate.
- **Frozen anchor = regression tripwire.** A tiny fixed slice (the original 7.40 test
  window) detects catastrophic forgetting / pipeline bugs. Slow decay is expected; a
  *sudden* drop alarms.
- **Embargo gap.** Because the feature aggregator carries each player's history
  forward, a ≥1-day embargo between train-end and val/test-start prevents adjacent-day
  label leakage.
- **Recency-weighted training.** Per-row loss weight `w = 0.5^(age_days/half_life)`
  (half-life ≈ 60d) with a small current-patch upweight (ψ ≈ 1.2–1.4). Eval/probe
  metrics stay **unweighted** so AUC remains interpretable.

## Relationship to `evaluation.md`

This is a deliberate, recorded adaptation of clause 3 ("consistency across comparable
experiments") for a non-stationary live repo. In this setting "comparable" means
"incumbent vs candidate on the same freshly-sealed window." The per-cycle re-seal is
the **expected mechanism**, documented in each cycle's metadata — it is *not* a
breaking-change ADR each time. Clauses 1 (test off-limits during search) and 2 (two
metric files) still hold verbatim.

## Consequences

- `promote.py` must load and score two models per cycle (incumbent + candidate).
- A weekly **from-scratch** anchor retrain runs alongside nightly fine-tunes to bound
  long-run drift/forgetting.

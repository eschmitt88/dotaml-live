# 0004 — Prequential (test-then-train) evaluation; retire the static holdout

- Status: accepted
- Date: 2026-06-05
- Supersedes: the *evaluation/promotion mechanism* of ADR 0002 (its recency-weighting
  and frozen-anchor tripwire are retained; the walk-forward static window is replaced).

## Context

ADR 0002's walk-forward split seals the freshest `test_days + val_days + 2·embargo`
(= 30 days by default) as a held-out block. That block is excluded from the model the
gate decides on, so the *served* model trails the present by ~30 days. In steady state
that's a tolerable delay (yesterday's holdout becomes tomorrow's train). **At a patch
boundary it is a 30-day blind spot**: the service runs the new meta for a month having
trained on almost no post-patch data.

We considered holding out a random %-of-matches per day to kill the lag. It doesn't
work here: the stateful aggregator carries each account's trajectory forward, so the
same players (and their day-specific latent state) bridge train and a random per-day
test → optimistically biased AUC. Worse, on a patch day both sides are post-patch, so
the metric is **blind to the pre→post generalization gap** — the exact risk we need to
measure.

## Decision

Adopt **prequential ("test-then-train") evaluation** and a **shadow head-to-head gate**;
drop the static held-out window as the promotion signal.

1. **Prequential monitoring (lag-free, honest).** As each new day *D* lands, score the
   current **live** model on *D* **before** *D* is folded into training. Because the
   model's parameters were fit on data ≤ *D*−1, day *D* is genuinely unseen and strictly
   in the future — no player bridges the split, and a patch shift is exposed directly
   (a pre-patch model scored on the first post-patch day). Append to a running
   `prequential.jsonl`. This is the live health signal.

2. **Shadow gate for promotion.** A candidate (warm-start fine-tune) and the incumbent
   are both scored on the most recent `eval_days` days that **neither has trained on**
   (the prequential eval window — the candidate is trained through `now − eval_days` for
   the decision). Promote iff the candidate beats the incumbent on that window by a
   margin, passes the probe thresholds, and does not regress on the **frozen anchor**
   (retained from 0002). `decide()` is unchanged; only *which data* defines the fresh
   window changes (next-days-after-cutoff instead of a static block).

3. **Refit-for-serving to kill the lag.** Once promoted, the candidate is fine-tuned the
   rest of the way through `now` (folding in the eval days) before it goes live. The
   eval days were withheld only for the *decision*, not the served *artifact* — standard
   refit-after-CV. **Serving lag → ~1 day** (was ~30).

4. **Patch handling.** The current-patch upweight (0002) is ramped up right after a
   detected patch and decays as post-patch data accumulates; a patch boundary may
   trigger an off-cadence retrain. Early in a patch, prequential simply reports higher
   variance on the thin post-patch window — it never hides the gap.

`eval_days` defaults to 7 (≈1.3M Turbo matches — ample for a tight AUC) and is the
evaluation horizon, **not** serving staleness.

## Why this is correct under drift

Prequential respects the data's time ordering, so the metric measures *extrapolation to
unseen future* (what serving needs), not *interpolation within a seen distribution*.
It is the standard evaluation for streaming non-stationary learners and is what AIRA2's
anti-overfit framing favors. The held-out-block lag disappears by construction.

## Consequences

- `seal_holdout` gains a prequential window; `retrain` scores live-on-new-day, gates on
  the eval window, then refits-for-serving. The `split` column in stored parquets
  becomes purely informational.
- A model is **un-evaluated on its own newest day** at serve time (you learn it's bad
  via the next prequential reading or the anchor tripwire). That is the inherent price
  of ~0 lag in a non-stationary system; the anchor + the next-day prequential check are
  the safety nets.
- HCE clauses still hold in spirit: the future is never used to fit the present, and the
  frozen anchor is never trained on.

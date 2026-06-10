# Improvement Loop Spec (Karpathy-style node optimization)

Every judgment node (runtime C/A) is an optimizable unit with a measurable objective function.
The loop optimizes against the objective, never against vibes, and every adopted change re-enters
the same gates as a human change.

## Objective function (on every C/A node card)

```json
"objective": {
  "primary":   { "metric": "golden_accuracy", "target": 0.9, "eval_ref": "process/evals/<wf>.golden.json" },
  "guardrails": [ { "metric": "hard_refuse_violations", "max": 0 },
                  { "metric": "leak_findings", "max": 0 } ],
  "cost":      { "metric": "model_cost_rank", "direction": "minimize" },
  "latency":   { "metric": "p95_seconds", "budget": null },
  "improvement_policy": {
    "auto_adopt": "cheaper_or_better_with_holdout_pass",
    "holdout_ref": ".holdout/evals/<node>.holdout.json",
    "re_enter_gates": true
  }
}
```

The golden set defines correct: grade `right` means `proposed` is truth; `edit`/`wrong` mean
`corrected` is truth. Ungraded examples never count. Grading stays human, async, forever.

## The loop (improve_node.py)

1. **Seal the split** (first run): every 3rd graded example goes to the holdout file under
   `.holdout/` (gitignored). The improver develops on the OPEN split; selection code paths
   cannot read the holdout. Same information-barrier pattern as Dark Factory QA.
2. **Champion** = the card's current (model, prompt_version). Evaluate on OPEN.
3. **Challengers**: cheaper models from `scripts/models.json` (always try the less expensive
   model), prompt-variant hook (lands with live executors), confidence-floor calibration from
   live run cards (5th percentile of observed confidence, 20+ runs, proposal-only).
4. **Rank** by (accuracy desc, cost_rank asc). A challenger wins only if better, or equal and
   cheaper.
5. **Holdout gate**: the winner runs once on the sealed split. No regression vs champion = pass.
6. **Adopt or queue** per policy: `cheaper_or_better_with_holdout_pass` + `re_enter_gates` →
   card updated, `exports/improvement_ledger.jsonl` appended, QA re-run immediately.
   Anything else → proposal at `exports/improvement_proposals/<node>.json`, human queue.

Hard limits: the improver never touches golden grades, hard-refuse lists, lanes, gates, or
supervision; never edits a live running agent (it edits design-time cards that re-enter gates);
never reads the holdout during selection.

## Eval lifecycle

Seed from the grill (S5) → accrete from every human review action (approve/edit/reject are
labeled examples) → version every change (a golden-set change is a card change) → comparisons
hold the set version constant → holdout stays sealed for the gate.

## Honesty note on executors

Until the S6 compiler ships live model executors, `evaluate_node.py` runs stub replay executors.
They prove the loop mechanics (splits, ranking, adoption, gates, ledger) and are labeled as such
in every eval artifact. They do not measure real model quality. When live executors land, the
same loop runs unchanged with real calls — that is the point of the contract.

## "Fine-tune" scope

Today: prompt optimization, threshold calibration, model selection. Weight-level fine-tuning
joins the challenger set when a provider fine-tune path is adopted; it enters the same loop
(train on open, gate on holdout, adopt by policy, re-enter QA).

#!/usr/bin/env python3
"""
Node eval harness: score a node configuration against its golden set (the objective function).

The golden set is the machine-readable definition of correct:
  grade=right -> `proposed` is the truth
  grade=edit  -> `corrected` is the truth
  grade=wrong -> `corrected` is the truth when present, else the example only counts
                 as correct if the node REFUSES (proposing the known-wrong answer scores 0)

Executors (pluggable; live model executors arrive with the S6 compiler):
  replay_proposed  -> returns the originally proposed answer (current-champion behavior stand-in)
  replay_truth     -> returns the truth (upper-bound calibration of the harness)
Executor choice NEVER changes the scoring rules. Stub executors measure harness correctness,
not real model quality — reports carry executor provenance so nobody mistakes one for the other.

Split discipline (Dark Factory): OPEN split for development/improvement; HOLDOUT split sealed in
.holdout/evals/<node>.holdout.json, read only when --split holdout is passed by the gate.

Usage: python scripts/evaluate_node.py <pkg> <node_id> [--split open|holdout] [--executor replay_proposed] [--model <id>]
Output: <pkg>/exports/evals/<node>__<split>__<executor>.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXECUTORS = ("replay_proposed", "replay_truth")
STUB_EXECUTORS = {"replay_proposed", "replay_truth"}


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def truth_of(example: dict) -> str | None:
    grade = example.get("grade")
    if grade == "right":
        return str(example.get("proposed", "")).strip()
    if grade in {"edit", "wrong"}:
        corrected = str(example.get("corrected", "")).strip()
        return corrected or None
    return None  # pending/ungraded examples never count toward the objective


def execute(executor: str, example: dict) -> str:
    if executor == "replay_proposed":
        return str(example.get("proposed", "")).strip()
    if executor == "replay_truth":
        return truth_of(example) or ""
    raise SystemExit(f"unknown executor {executor!r} (live executors land with the S6 compiler)")


def split_examples(pkg: Path, node_card: dict, split: str) -> list[dict]:
    eval_ref = node_card.get("eval_ref") or node_card.get("objective", {}).get("primary", {}).get("eval_ref")
    if not eval_ref:
        raise SystemExit("node has no eval_ref / objective — not a judgment node or card incomplete")
    full = load(pkg / eval_ref, [])
    full = full if isinstance(full, list) else full.get("examples", [])
    holdout_ref = (node_card.get("objective", {}).get("improvement_policy", {})
                   .get("holdout_ref", f".holdout/evals/{node_card.get('node_id')}.holdout.json"))
    holdout_keys = {e.get("record_ref") for e in load(pkg / holdout_ref, [])}
    if split == "holdout":
        chosen = [e for e in full if e.get("record_ref") in holdout_keys]
        if not chosen:
            raise SystemExit("holdout split empty — run improve_node.py once to seal a split")
        return chosen
    return [e for e in full if e.get("record_ref") not in holdout_keys]


def score(examples: list[dict], executor: str) -> dict:
    per, correct, graded = [], 0, 0
    for e in examples:
        t = truth_of(e)
        if t is None:
            per.append({"record_ref": e.get("record_ref"), "result": "skipped_ungraded"})
            continue
        graded += 1
        out = execute(executor, e)
        ok = out == t and not (e.get("grade") == "wrong" and out == str(e.get("proposed", "")).strip())
        correct += int(ok)
        per.append({"record_ref": e.get("record_ref"), "result": "correct" if ok else "incorrect"})
    return {"n_graded": graded, "n_correct": correct,
            "golden_accuracy": round(correct / graded, 4) if graded else None,
            "per_example": per}


def cost_rank_of(model: str | None) -> int | None:
    from factory_config import model_ladder
    for m in model_ladder():
        if m["id"] == model:
            return m["cost_rank"]
    return None


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        raise SystemExit("Usage: python scripts/evaluate_node.py <package_dir> <node_id> "
                         "[--split open|holdout] [--executor replay_proposed] [--model <id>]")
    pkg, node_id = Path(args[0]).resolve(), args[1]

    def opt(name: str, default: str) -> str:
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

    split = opt("--split", "open")
    executor = opt("--executor", "replay_proposed")
    card = load(pkg / "process" / "nodes" / f"{node_id}.aac.json")
    if not card:
        raise SystemExit(f"no card for {node_id}")
    model = opt("--model", str(card.get("model", "")))

    examples = split_examples(pkg, card, split)
    executor_kind = "stub" if executor in STUB_EXECUTORS else "real"
    result = {
        "node_id": node_id, "split": split, "executor": executor,
        "executor_kind": executor_kind,
        "executor_note": ("stub executor: measures harness correctness, NOT live model quality"
                          if executor_kind == "stub" else "real executor: model-differentiating evidence"),
        "model_differentiating_evidence": executor_kind == "real",
        "certification_eligible": executor_kind == "real",
        "certification_blockers": ([] if executor_kind == "real"
                                   else ["stub_executor_no_model_differentiating_evidence"]),
        "model": model, "model_cost_rank": cost_rank_of(model),
        "prompt_version": card.get("prompt_version"),
        **score(examples, executor),
        "objective_target": (card.get("objective", {}).get("primary", {}) or {}).get("target"),
    }
    out = pkg / "exports" / "evals" / f"{node_id}__{split}__{executor}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{node_id} [{split}/{executor}] golden_accuracy={result['golden_accuracy']} "
          f"(n={result['n_graded']}) model={model or 'TODO'} -> {out.relative_to(pkg)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Karpathy-style improvement loop for one node: optimize against the node's measurable
objective function, never against vibes.

Loop per invocation:
  1. Seal the split (first run): every 3rd graded example -> .holdout/evals/<node>.holdout.json.
     The improver develops on the OPEN split only; the sealed holdout is touched once, at the
     adoption gate. Selection code paths physically cannot read the holdout file.
  2. Champion = the node card's current (model, prompt_version). Evaluate on OPEN.
  3. Challengers = cheaper models from the ladder (config > machine scan > built-ins; always try the less
     expensive model), plus prompt-variant and threshold-calibration hooks (live data from
     run cards feeds threshold calibration when present).
  4. Rank by (golden_accuracy desc, cost_rank asc). Candidate wins if better, or equal-and-cheaper.
  5. Gate: winner runs ONCE on the sealed holdout. No regression vs champion holdout -> pass.
  6. Outcome per automation_policy + objective.improvement_policy:
       auto_adopt matched + holdout pass -> card updated (model/prompt_version), ledger entry,
       QA re-entry (re_enter_gates) -- the diff goes through the same gates as a human change.
       otherwise -> proposal queued at exports/improvement_proposals/<node>.json (human-over-loop).
  Never touches golden grades. Never edits hard_refuse, lanes, or gates. Never reads holdout
  during selection.

Usage: python scripts/improve_node.py <package_dir> <node_id> [--propose-only]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from evaluate_node import load, score, truth_of, cost_rank_of

SCRIPTS = Path(__file__).resolve().parent


def graded_examples(pkg: Path, eval_ref: str) -> list[dict]:
    full = load(pkg / eval_ref, [])
    full = full if isinstance(full, list) else full.get("examples", [])
    return [e for e in full if truth_of(e) is not None]


def seal_holdout(pkg: Path, node_id: str, examples: list[dict], holdout_ref: str) -> Path:
    hp = pkg / holdout_ref
    if not hp.exists():
        sealed = [{"record_ref": e.get("record_ref"), "sealed": True} for e in examples[2::3]] \
                 or ([{"record_ref": examples[-1].get("record_ref"), "sealed": True}] if examples else [])
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(json.dumps(sealed, indent=2) + "\n", encoding="utf-8")
        print(f"sealed holdout: {len(sealed)} example(s) -> {holdout_ref}")
    return hp


def run_eval(pkg: Path, node_id: str, split: str, model: str) -> dict:
    r = subprocess.run([sys.executable, str(SCRIPTS / "evaluate_node.py"), str(pkg), node_id,
                        "--split", split, "--executor", "replay_proposed", "--model", model],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"eval failed: {r.stdout}{r.stderr}")
    return load(pkg / "exports" / "evals" / f"{node_id}__{split}__replay_proposed.json", {})


def calibrate_threshold(pkg: Path, node_id: str) -> dict | None:
    """Live-data hook: when run cards exist, recommend a confidence floor from the observed
    distribution (5th percentile of correct runs). Proposal-only; floors are versioned gates."""
    cards = sorted((pkg / "process" / "run-cards" / node_id).glob("*.json"))
    confidences = [c.get("confidence") for c in (load(p, {}) for p in cards)
                   if isinstance(c.get("confidence"), (int, float))]
    if len(confidences) < 20:
        return None
    confidences.sort()
    return {"recommended_floor": confidences[max(0, len(confidences) // 20)],
            "basis": f"{len(confidences)} run cards, 5th percentile",
            "note": "re-calibrate on every (model, prompt) change"}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        raise SystemExit("Usage: python scripts/improve_node.py <package_dir> <node_id> [--propose-only]")
    pkg, node_id = Path(args[0]).resolve(), args[1]
    propose_only = "--propose-only" in sys.argv

    card_path = pkg / "process" / "nodes" / f"{node_id}.aac.json"
    card = load(card_path)
    if not card or card.get("runtime_mode") not in {"C", "A"}:
        raise SystemExit(f"{node_id}: improvement loop applies to judgment (C/A) nodes only")
    objective = card.get("objective") or {}
    policy = objective.get("improvement_policy", {})
    eval_ref = card.get("eval_ref") or objective.get("primary", {}).get("eval_ref")
    examples = graded_examples(pkg, eval_ref)
    if len(examples) < 3:
        raise SystemExit(f"{node_id}: needs >=3 graded golden examples (have {len(examples)}). "
                         "Grading stays human — queue it.")

    holdout_ref = policy.get("holdout_ref", f".holdout/evals/{node_id}.holdout.json")
    seal_holdout(pkg, node_id, examples, holdout_ref)

    champion_model = str(card.get("model", ""))
    champion_is_todo = champion_model.startswith("TODO") or not champion_model
    from factory_config import model_ladder
    ladder = model_ladder()
    champion_rank = cost_rank_of(champion_model) or max((m["cost_rank"] for m in ladder), default=3)

    champion = run_eval(pkg, node_id, "open", champion_model or "unset")
    candidates = []
    for m in ladder:
        if m["cost_rank"] <= champion_rank and m["id"] != champion_model:
            ev = run_eval(pkg, node_id, "open", m["id"])
            candidates.append({"change": "model_swap", "model": m["id"],
                               "cost_rank": m["cost_rank"], "open": ev})
    # prompt-variant hook: arrives with the S6 compiler + live executors (logged, not faked)
    threshold = calibrate_threshold(pkg, node_id)

    def key(c):
        return (-(c["open"].get("golden_accuracy") or 0), c["cost_rank"])

    candidates.sort(key=key)
    champ_acc = champion.get("golden_accuracy") or 0
    winner = None
    for c in candidates:
        acc = c["open"].get("golden_accuracy") or 0
        if acc > champ_acc or (acc == champ_acc and c["cost_rank"] < champion_rank):
            winner = c
            break

    decision = {"node_id": node_id, "champion": {"model": champion_model or "unset",
                "cost_rank": champion_rank, "open": champion},
                "candidates": candidates, "threshold_calibration": threshold,
                "executor_note": "stub/replay executors prove loop mechanics only; auto-adoption requires real executor evidence"}

    if winner:
        hold_champ = run_eval(pkg, node_id, "holdout", champion_model or "unset")
        hold_win = run_eval(pkg, node_id, "holdout", winner["model"])
        holdout_pass = (hold_win.get("golden_accuracy") or 0) >= (hold_champ.get("golden_accuracy") or 0)
        real_evidence = bool(
            champion.get("model_differentiating_evidence")
            and winner["open"].get("model_differentiating_evidence")
            and hold_champ.get("model_differentiating_evidence")
            and hold_win.get("model_differentiating_evidence")
        )
        decision["winner"] = {**winner, "holdout": hold_win, "holdout_pass": holdout_pass,
                              "model_differentiating_evidence": real_evidence}
        auto_rule = policy.get("auto_adopt", "never")
        may_adopt = (not propose_only and real_evidence and holdout_pass
                     and auto_rule == "cheaper_or_better_with_holdout_pass"
                     and policy.get("re_enter_gates", True))
        if may_adopt and (champion_is_todo or winner["cost_rank"] < champion_rank
                          or (winner["open"].get("golden_accuracy") or 0) > champ_acc):
            card["model"] = winner["model"]
            card["model_provenance"] = {"source": "improver",
                                        "basis": f"open acc {winner['open'].get('golden_accuracy')} vs champion {champ_acc}; "
                                                 f"cost rank {winner['cost_rank']} vs {champion_rank}; holdout pass"}
            card_path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            ledger = pkg / "exports" / "improvement_ledger.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"node_id": node_id, "adopted": winner["model"],
                                    "replaced": champion_model or "unset",
                                    "evidence": decision["winner"]["holdout"]}) + "\n")
            decision["outcome"] = "adopted_and_re_entering_gates"
            subprocess.run([sys.executable, str(SCRIPTS / "qa_agent_package.py"), str(pkg)],
                           capture_output=True, text=True)
        else:
            if not real_evidence:
                decision["outcome"] = "proposal_queued_adoption_blocked_stub_executor"
                ledger = pkg / "exports" / "improvement_ledger.jsonl"
                ledger.parent.mkdir(parents=True, exist_ok=True)
                with ledger.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"node_id": node_id,
                                        "adopted": None,
                                        "candidate": winner["model"],
                                        "reason": "adoption_blocked_stub_executor",
                                        "evidence": decision["winner"]["holdout"]}) + "\n")
            else:
                decision["outcome"] = "proposal_queued (policy/holdout/flag)"
    else:
        decision["outcome"] = "champion_stands"

    out = pkg / "exports" / "improvement_proposals" / f"{node_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{node_id}: {decision['outcome']}" + (f" -> {winner['model']}" if winner else ""))
    print(f"  details: {out.relative_to(pkg)}")


if __name__ == "__main__":
    main()

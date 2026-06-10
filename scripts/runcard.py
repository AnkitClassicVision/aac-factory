#!/usr/bin/env python3
"""
Run-card contract: per-run telemetry proof for every node execution.

Design-time cards say what a node MAY do; run cards prove what it DID.
Compiled agents (S6) import this module so telemetry is part of the process,
not an afterthought. Human-over-the-loop supervision reads the review queue,
never blocks individual runs.

Usage (from an executing agent):
    from runcard import new_run_card, write_run_card
    rc = new_run_card(workflow="x", node_id="x-classify", runtime_mode="C", run_id="...", ts_start="...")
    ... fill outcome fields ...
    write_run_card(package_dir, rc)   # validates, writes, queues escalations
"""

from __future__ import annotations

import json
from pathlib import Path

RUN_CARD_REQUIRED = [
    "run_id", "workflow", "node_id", "runtime_mode", "actual_lane",
    "ts_start", "ts_end", "input_ref", "output_ref",
    "gate_outcomes", "refuse", "external_actions_taken", "source_refs",
]
GATE_KEYS = ("input", "output", "cross_check", "action")
ESCALATION_REASONS = ("hard_refuse", "confidence_below_floor", "gate_failure", "qa_block", "drift_alarm")


def new_run_card(**fields) -> dict:
    card = {
        "run_id": "", "workflow": "", "node_id": "", "runtime_mode": "",
        "actual_lane": "internal_artifact_only",
        "ts_start": "", "ts_end": "",
        "input_ref": "", "output_ref": "",
        "gate_outcomes": {k: "not_run" for k in GATE_KEYS},
        "confidence": None,
        "model": None, "prompt_version": None,
        "cost": {"tokens_in": 0, "tokens_out": 0, "usd_estimate": None},
        "refuse": {"refused": False, "hard": False, "reason": ""},
        "external_actions_taken": 0,
        "source_refs": [],
        "escalation": {"escalated": False, "reason": ""},
        "qa": {"sampled": False, "verdict": None},
    }
    card.update(fields)
    return card


def validate_run_card(card: dict) -> list[str]:
    errors = [f"missing/empty: {f}" for f in RUN_CARD_REQUIRED
              if f not in card or card[f] in (None, "", {})]
    gates = card.get("gate_outcomes") or {}
    errors += [f"gate_outcomes missing key: {k}" for k in GATE_KEYS if k not in gates]
    if card.get("runtime_mode") in {"C", "A"}:
        for f in ("confidence", "model", "prompt_version"):
            if card.get(f) in (None, ""):
                errors.append(f"C/A run card requires {f}")
    refuse = card.get("refuse") or {}
    if refuse.get("refused") and not refuse.get("reason"):
        errors.append("refusal requires a reason (refuse is first-class, and logged)")
    ext = card.get("external_actions_taken")
    if not isinstance(ext, int) or ext < 0:
        errors.append("external_actions_taken must be an integer >= 0")
    esc = card.get("escalation") or {}
    if esc.get("escalated") and esc.get("reason") not in ESCALATION_REASONS:
        errors.append(f"escalation reason must be one of {ESCALATION_REASONS}")
    return errors


def write_run_card(package_dir: str | Path, card: dict) -> Path:
    pkg = Path(package_dir)
    errors = validate_run_card(card)
    if errors:
        raise ValueError("run card invalid: " + "; ".join(errors))
    out_dir = pkg / "process" / "run-cards" / card["node_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{card['run_id']}.json"
    out.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Human-over-the-loop: escalations and refusals land in the async review queue.
    if (card.get("escalation") or {}).get("escalated") or (card.get("refuse") or {}).get("refused"):
        q = pkg / "process" / "run-cards" / "_review_queue"
        q.mkdir(parents=True, exist_ok=True)
        (q / f"{card['node_id']}__{card['run_id']}.json").write_text(
            json.dumps({"node_id": card["node_id"], "run_id": card["run_id"],
                        "reason": (card.get("escalation") or {}).get("reason")
                        or ("hard_refuse" if (card.get("refuse") or {}).get("hard") else "refused"),
                        "card": str(out.relative_to(pkg))}, indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print("runcard.py is a library: import new_run_card / validate_run_card / write_run_card")

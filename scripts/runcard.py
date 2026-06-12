#!/usr/bin/env python3
"""
Run-card contract: per-run telemetry proof for every node execution.

v0.3 runtime-truth rule: unknown telemetry is honest; fake zero is not. Model
truth is split into requested_model vs actual_model, and unverified model identity
makes the run non-certifying without blocking shadow/debug execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RUN_CARD_REQUIRED = [
    "run_id", "workflow", "node_id", "runtime_mode", "actual_lane",
    "ts_start", "ts_end", "input_ref", "output_ref",
    "gate_outcomes", "refuse", "external_actions_taken", "source_refs",
]
GATE_KEYS = ("input", "output", "cross_check", "action")
ESCALATION_REASONS = ("hard_refuse", "confidence_below_floor", "gate_failure", "qa_block", "drift_alarm")
UNKNOWN_USAGE = {"tokens_in": "unknown", "tokens_out": "unknown", "cost_usd": "unknown",
                 "usage_source": "unknown", "actual_usage_available": False}


def _usage(fields: dict[str, Any]) -> dict[str, Any]:
    supplied = dict(fields.pop("usage", {}) or {})
    legacy_cost_raw = fields.pop("cost", None)
    legacy_cost = legacy_cost_raw if isinstance(legacy_cost_raw, dict) else {}
    usage = dict(UNKNOWN_USAGE)
    usage.update(supplied)
    if legacy_cost:
        if legacy_cost.get("tokens_in") not in (None, 0):
            usage["tokens_in"] = legacy_cost.get("tokens_in")
        if legacy_cost.get("tokens_out") not in (None, 0):
            usage["tokens_out"] = legacy_cost.get("tokens_out")
        if legacy_cost.get("usd_estimate") not in (None, 0):
            usage["cost_usd"] = legacy_cost.get("usd_estimate")
            usage["usage_source"] = usage.get("usage_source") or "legacy_estimate"
    return usage


def new_run_card(**fields) -> dict:
    fields = dict(fields)
    model = fields.get("model")
    requested_model = fields.get("requested_model", model)
    usage = _usage(fields)
    card = {
        "run_id": "", "workflow": "", "node_id": "", "runtime_mode": "",
        "run_kind": "node",
        "parent_run_id": None,
        "candidate_ref": None,
        "actual_lane": "internal_artifact_only",
        "ts_start": "", "ts_end": "",
        "input_ref": "", "output_ref": "",
        "gate_outcomes": {k: "not_run" for k in GATE_KEYS},
        "confidence": None,
        "model": requested_model,
        "requested_model": requested_model,
        "actual_model": fields.get("actual_model", "unknown"),
        "model_verified": bool(fields.get("model_verified", False)),
        "verification_source": fields.get("verification_source", "unverified"),
        "executor": fields.get("executor", "unknown"),
        "adapter_version": fields.get("adapter_version", "unknown"),
        "prompt_version": None,
        "usage": usage,
        "cost": {"tokens_in": usage.get("tokens_in", "unknown"),
                 "tokens_out": usage.get("tokens_out", "unknown"),
                 "usd_estimate": usage.get("cost_usd", "unknown")},
        "certification_eligible": fields.get("certification_eligible", True),
        "certification_blockers": list(fields.get("certification_blockers", []) or []),
        "refuse": {"refused": False, "hard": False, "reason": ""},
        "external_actions_taken": 0,
        "source_refs": [],
        "escalation": {"escalated": False, "reason": ""},
        "qa": {"sampled": False, "verdict": None},
    }
    # Merge remaining fields after defaults, but keep normalized usage/model fields coherent.
    fields.pop("model", None)
    fields.pop("requested_model", None)
    fields.pop("actual_model", None)
    fields.pop("model_verified", None)
    fields.pop("verification_source", None)
    fields.pop("executor", None)
    fields.pop("adapter_version", None)
    fields.pop("certification_eligible", None)
    fields.pop("certification_blockers", None)
    card.update(fields)
    if card["runtime_mode"] in {"C", "A"}:
        blockers = set(card.get("certification_blockers") or [])
        if not card.get("model_verified"):
            blockers.add("model_identity_unverified")
        if blockers:
            card["certification_blockers"] = sorted(blockers)
            card["certification_eligible"] = False
    return card


def validate_run_card(card: dict) -> list[str]:
    errors = [f"missing/empty: {f}" for f in RUN_CARD_REQUIRED
              if f not in card or card[f] in (None, "", {})]
    gates = card.get("gate_outcomes") or {}
    errors += [f"gate_outcomes missing key: {k}" for k in GATE_KEYS if k not in gates]
    if card.get("runtime_mode") in {"C", "A"}:
        for f in ("confidence", "requested_model", "prompt_version"):
            if card.get(f) in (None, ""):
                errors.append(f"C/A run card requires {f}")
        usage = card.get("usage") or {}
        for f in ("tokens_in", "tokens_out", "cost_usd", "usage_source", "actual_usage_available"):
            if f not in usage:
                errors.append(f"usage missing {f}")
        # Fake zero is the v0.2 bug. Zero is valid only when usage source says real usage was captured.
        if usage.get("actual_usage_available") is False and (usage.get("tokens_in") == 0 or usage.get("tokens_out") == 0):
            errors.append("unknown usage must be recorded as 'unknown', not fake 0")
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

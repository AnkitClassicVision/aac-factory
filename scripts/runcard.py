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
TBR_TRACE_FIELDS = [
    "run_id", "workflow", "node_id", "runtime_mode", "input_ref", "output_ref",
    "source_refs", "definition_refs", "permission_decision", "gate_outcomes", "refuse",
    "external_actions_taken",
]


def _normalize_tbr(tbr: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "definition_refs": [],
        "permission_decision": {"allowed": None, "policy_ref": "", "reason": ""},
        "semantic_source_refs": [],
        "recipient_ref": "",
        "audit_notes": "",
        "trace_fields": list(TBR_TRACE_FIELDS),
    }
    if isinstance(tbr, dict):
        for k, v in tbr.items():
            if k == "permission_decision" and isinstance(v, dict):
                merged = dict(base["permission_decision"])
                merged.update(v)
                base[k] = merged
            else:
                base[k] = v
    return base


def _tbr_certification_blockers(tbr: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not tbr.get("definition_refs"):
        blockers.append("tbr_definition_refs_missing")
    if not tbr.get("semantic_source_refs"):
        blockers.append("tbr_semantic_source_refs_missing")
    pd = tbr.get("permission_decision") or {}
    if pd.get("allowed") is None or not pd.get("policy_ref"):
        blockers.append("tbr_permission_decision_incomplete")
    if set(TBR_TRACE_FIELDS) - set(tbr.get("trace_fields") or []):
        blockers.append("tbr_trace_fields_incomplete")
    if "TODO" in json.dumps(tbr, ensure_ascii=False):
        blockers.append("tbr_contains_todo")
    return blockers


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
    tbr_required = bool(fields.pop("tbr_required", False))
    tbr = _normalize_tbr(fields.pop("tbr", None))
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
        "tbr_required": tbr_required,
        "tbr": tbr,
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
    if card.get("tbr_required"):
        blockers = set(card.get("certification_blockers") or [])
        blockers.update(_tbr_certification_blockers(card.get("tbr") or {}))
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
    if card.get("tbr_required"):
        tbr = _normalize_tbr(card.get("tbr") or {})
        card["tbr"] = tbr
        for f in ("definition_refs", "permission_decision", "semantic_source_refs", "trace_fields"):
            if f not in tbr or tbr[f] in (None, "", [], {}):
                errors.append(f"TBR run proof requires {f}")
        pd = tbr.get("permission_decision") or {}
        if pd.get("allowed") is None:
            errors.append("TBR permission_decision.allowed must be true/false")
        if not pd.get("policy_ref"):
            errors.append("TBR permission_decision.policy_ref required")
        blockers = set(card.get("certification_blockers") or [])
        blockers.update(_tbr_certification_blockers(tbr))
        if blockers:
            card["certification_blockers"] = sorted(blockers)
            card["certification_eligible"] = False
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

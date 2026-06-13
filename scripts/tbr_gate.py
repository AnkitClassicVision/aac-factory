#!/usr/bin/env python3
"""
Translator / Bouncer / Recorder (TBR) gate helpers for AAC Factory.

TBR is not a new AAC framework layer. It is the factory-level production-readiness
proof shape that makes the existing GROUNDED / GATED / OBSERVED / GOVERNED claims
machine-checkable in cards and visible in run cards.
"""

from __future__ import annotations

import json
from typing import Any

TBR_TRACE_FIELDS = [
    "run_id",
    "workflow",
    "node_id",
    "runtime_mode",
    "input_ref",
    "output_ref",
    "source_refs",
    "definition_refs",
    "permission_decision",
    "gate_outcomes",
    "refuse",
    "external_actions_taken",
]

SENSITIVE_HINTS = (
    "phi", "pii", "hr", "salary", "finance", "financial", "payment", "payroll",
    "patient", "customer", "client", "credential", "secret", "token", "external",
    "send", "write", "delete", "publish", "merge", "deploy", "enroll", "sms", "email",
)


def todo(field: str) -> str:
    return f"TODO: {field}"


def has_todo(value: Any) -> bool:
    return "TODO" in json.dumps(value, ensure_ascii=False)


def _text_blob(*values: Any) -> str:
    return " ".join(json.dumps(v, ensure_ascii=False).lower() for v in values if v is not None)


def tbr_exemption_valid(workflow: dict) -> bool:
    gate = workflow.get("tbr_gate") or {}
    lane = str(workflow.get("max_lane") or "")
    reason = gate.get("exemption_reason")
    reason_clean = reason.strip() if isinstance(reason, str) else ""
    return bool(gate.get("required") is False
                and gate.get("non_certifying") is True
                and reason_clean and "TODO" not in reason_clean
                and lane in {"design_scaffold", "internal_artifact_only", "shadow"})


def tbr_required_for_workflow(workflow: dict) -> bool:
    """Default-on for factory agent packages; opt-out is allowed only for explicit non-certifying shadow/design packages."""
    gate = workflow.get("tbr_gate") or {}
    if gate.get("required") is False:
        return not tbr_exemption_valid(workflow)
    return True


def tbr_required_for_node(workflow: dict, node_card: dict) -> bool:
    workflow_required = tbr_required_for_workflow(workflow)
    gate = node_card.get("tbr_gate") or {}
    # A node may only opt out when the whole workflow carries an explicit non-certifying exemption.
    if gate.get("required") is False:
        return False if not workflow_required else True
    if isinstance(gate.get("required"), bool):
        return bool(gate["required"])
    if not workflow_required:
        return False
    blob = _text_blob(workflow, node_card)
    return node_card.get("runtime_mode") in {"C", "A", "D", "H"} or any(h in blob for h in SENSITIVE_HINTS)


def default_workflow_tbr_gate(workflow: dict | None = None) -> dict:
    workflow = workflow or {}
    metrics = (workflow.get("observability") or {}).get("metrics") or []
    metric_defs = [
        {
            "term": str(m),
            "definition_ref": todo(f"canonical definition for {m}"),
            "source_of_truth_ref": todo(f"source of truth for {m}"),
        }
        for m in metrics if isinstance(m, str) and not m.startswith("TODO")
    ] or [{
        "term": todo("metric / business term"),
        "definition_ref": todo("semantic layer, policy doc, or source-owner definition"),
        "source_of_truth_ref": todo("system/table/view/API that owns this definition"),
    }]
    return {
        "required": True,
        "translator": {
            "canonical_definitions": metric_defs,
            "source_of_truth_refs": [todo("top source of truth ref(s) for this workflow")],
            "raw_query_policy": "agents_may_not_query_raw_production_tables_without_a_semantic_or_policy_gate",
        },
        "bouncer": {
            "effective_permission_model": "user_via_agent",
            "sensitive_systems": [todo("HR / finance / PII / PHI / client-sensitive systems, or none")],
            "policy_engine_ref": todo("runtime authz policy/proxy/gate ref"),
            "task_scoped_tokens_required": True,
        },
        "recorder": {
            "run_card_required": True,
            "trace_fields": list(TBR_TRACE_FIELDS),
            "retention_class": todo("log retention class, e.g. >=6 months if regulated"),
            "tamper_evidence": todo("append-only store / checksum / WORM / external log sink"),
            "review_cadence": todo("operator/audit review cadence"),
        },
    }


def default_node_tbr_gate(workflow: dict | None = None, node_card: dict | None = None) -> dict:
    workflow = workflow or {}
    node_card = node_card or {}
    node_id = node_card.get("node_id", "node")
    source = ((node_card.get("input_contract") or {}).get("source") or todo(f"source ref for {node_id}"))
    return {
        "required": True,
        "translator": {
            "definition_refs": [todo(f"semantic definition(s) this node relies on, or explicit n/a for {node_id}")],
            "source_of_truth_refs": [source],
            "source_refs_required": True,
            "raw_query_allowed": False,
        },
        "bouncer": {
            "agent_identity": todo(f"agent identity/service principal for {node_id}"),
            "human_identity_passthrough": "required",
            "allowed_resources": [todo(f"specific resources/actions allowed for {node_id}")],
            "forbidden_resources": [todo("sensitive resources/actions this node must never touch")],
            "effective_permission_path": todo("user -> agent -> policy decision -> resource/action"),
            "task_scope": todo("verbs/resources/time window for this run"),
        },
        "recorder": {
            "run_card_required": True,
            "trace_fields": list(TBR_TRACE_FIELDS),
            "permission_decision_logged": True,
            "definition_refs_logged": True,
            "source_refs_logged": True,
        },
    }


def _missing_or_empty(root: dict, dotted: str) -> bool:
    cur: Any = root
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return True
        cur = cur[part]
    return cur in (None, "", [], {})


def _validate_block(prefix: str, gate: dict, required_paths: list[str]) -> list[str]:
    errors: list[str] = []
    for p in required_paths:
        if _missing_or_empty(gate, p):
            errors.append(f"{prefix}: {p} missing/empty")
        else:
            cur: Any = gate
            for part in p.split("."):
                cur = cur[part]
            if has_todo(cur):
                errors.append(f"{prefix}: {p} still TODO")
    return errors


def validate_workflow_tbr(workflow: dict) -> list[str]:
    if not tbr_required_for_workflow(workflow):
        return []
    gate = workflow.get("tbr_gate") or {}
    if not gate:
        return ["workflow tbr_gate missing (Translator/Bouncer/Recorder proof required)"]
    return _validate_block("workflow tbr_gate", gate, [
        "translator.canonical_definitions",
        "translator.source_of_truth_refs",
        "translator.raw_query_policy",
        "bouncer.effective_permission_model",
        "bouncer.policy_engine_ref",
        "recorder.run_card_required",
        "recorder.trace_fields",
        "recorder.retention_class",
        "recorder.tamper_evidence",
        "recorder.review_cadence",
    ])


def validate_node_tbr(workflow: dict, node_card: dict) -> list[str]:
    nid = node_card.get("node_id", "<node>")
    if not tbr_required_for_node(workflow, node_card):
        return []
    gate = node_card.get("tbr_gate") or {}
    if not gate:
        return [f"{nid}: tbr_gate missing (Translator/Bouncer/Recorder proof required)"]
    return _validate_block(f"{nid}: tbr_gate", gate, [
        "translator.definition_refs",
        "translator.source_of_truth_refs",
        "bouncer.agent_identity",
        "bouncer.human_identity_passthrough",
        "bouncer.allowed_resources",
        "bouncer.forbidden_resources",
        "bouncer.effective_permission_path",
        "bouncer.task_scope",
        "recorder.run_card_required",
        "recorder.trace_fields",
        "recorder.permission_decision_logged",
    ])


def tbr_status(workflow: dict, node_cards: list[dict]) -> dict:
    blockers = validate_workflow_tbr(workflow)
    for c in node_cards:
        blockers.extend(validate_node_tbr(workflow, c))
    return {
        "required": tbr_required_for_workflow(workflow),
        "blockers": blockers,
        "todo_blockers": sum(1 for b in blockers if "TODO" in b),
        "missing_blockers": sum(1 for b in blockers if "missing" in b or "empty" in b),
        "complete": not blockers,
    }

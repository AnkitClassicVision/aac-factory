#!/usr/bin/env python3
"""
Translator / Bouncer / Recorder (TBR) gate helpers for AAC Factory.

TBR is not a new AAC framework layer. It is the factory-level production-readiness
proof shape that makes the existing GROUNDED / GATED / OBSERVED / GOVERNED claims
machine-checkable in cards and visible in run cards.
"""

from __future__ import annotations

import json
import re
from typing import Any

TBR_TRACE_FIELDS = [
    "run_id",
    "workflow",
    "node_id",
    "runtime_mode",
    "input_ref",
    "output_ref",
    "prompt_ref",
    "prompt_version",
    "response_ref",
    "tool_call_refs",
    "user_ref",
    "recipient_ref",
    "source_refs",
    "definition_refs",
    "permission_decision",
    "gate_outcomes",
    "refuse",
    "external_actions_taken",
    "retention_class",
    "tamper_evidence",
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
            "retention_class": todo("log retention class inherited from workflow or stricter"),
            "tamper_evidence": todo("append-only store / checksum / WORM / external log sink"),
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


def _value(root: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = root
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _is_true(value: Any) -> bool:
    return value is True


def _is_false(value: Any) -> bool:
    return value is False


def _listish(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _bad_permission_blob(value: Any) -> bool:
    blob = _text_blob(value)
    bad_terms = (
        "god-mode", "god mode", "shared_service", "shared service", "shared-service",
        "shared_service_account", "shared service account", "shared-service-account", "all-powerful",
        "all_resources", "all resources", "any resources", "any resource", "any request",
        "all tables", "entire crm", "entire database", "everything",
        "admin everywhere", "root access", "forever", "unbounded", "*:*", ":*", "/*", ".*",
    )
    if any(t in blob for t in bad_terms):
        return True
    if re.search(r'(?<![a-z0-9_-])\*(?![a-z0-9_-])', blob):
        return True
    if re.search(r'(?<![a-z0-9_-])(all|any|every)(?![a-z0-9_-])', blob):
        return True
    if re.search(r'\b(read|write|access|query|fetch)\s+(all|any|every)\b', blob):
        return True
    return False


def _placeholder_blob(value: Any) -> bool:
    blob = _text_blob(value)
    placeholder_terms = (
        "TODO", "placeholder", "tbd", "to be decided", "n/a", "not applicable",
        "unknown", "fill me", "owner picks", "dummy", "sample", "example",
        "\"none\"", "'none'", ": none", "= none", "...",
    )
    return any(t.lower() in blob for t in placeholder_terms)


def _unsafe_tbr_value(value: Any) -> bool:
    return _placeholder_blob(value) or _bad_permission_blob(value)


def _raw_query_policy_blocks_raw_sql(policy: Any) -> bool:
    text = str(policy or "").strip().lower()
    if not text or has_todo(text):
        return False
    raw_terms = ("raw", "sql", "query", "queries", "production", "prod", "table", "tables", "direct")
    allow_terms = ("allow", "allowed", "allows", "permit", "permitted", "permits", "can", "may", "enabled", "direct")
    forbid_terms = (
        "semantic_gate", "semantic gate", "policy gate", "may_not_query_raw",
        "must_not_query_raw", "not query raw", "no raw", "forbid", "forbidden",
        "ban", "blocked", "without_a_semantic_or_policy_gate",
        "agents_may_not_query_raw_production_tables_without_a_semantic_or_policy_gate",
    )
    if any(t in text for t in raw_terms) and any(re.search(rf'(?<![a-z0-9_-]){re.escape(t)}(?![a-z0-9_-])', text) for t in allow_terms):
        return False
    return any(marker in text for marker in forbid_terms)


def _validate_trace_fields(prefix: str, trace_fields: Any) -> list[str]:
    fields = set(_listish(trace_fields))
    missing = sorted(set(TBR_TRACE_FIELDS) - fields)
    if missing:
        return [f"{prefix}: recorder.trace_fields missing {missing}"]
    return []


def _require_true(errors: list[str], prefix: str, gate: dict, dotted: str) -> None:
    value = _value(gate, dotted)
    if not _is_true(value):
        errors.append(f"{prefix}: {dotted} must be true")


def _require_false(errors: list[str], prefix: str, gate: dict, dotted: str) -> None:
    value = _value(gate, dotted)
    if not _is_false(value):
        errors.append(f"{prefix}: {dotted} must be false")


def _require_exact(errors: list[str], prefix: str, gate: dict, dotted: str, expected: str) -> None:
    value = str(_value(gate, dotted, "") or "").strip().lower()
    if value != expected:
        errors.append(f"{prefix}: {dotted} must be {expected}")


def _require_bounded(errors: list[str], prefix: str, gate: dict, dotted: str) -> None:
    value = _value(gate, dotted)
    if _bad_permission_blob(value):
        errors.append(f"{prefix}: {dotted} appears unbounded or shared/god-mode")


def _require_concrete(errors: list[str], prefix: str, gate: dict, dotted: str) -> None:
    value = _value(gate, dotted)
    if has_todo(value):
        return
    if _unsafe_tbr_value(value):
        errors.append(f"{prefix}: {dotted} must be concrete, non-placeholder, and least-privilege scoped")


def _validate_workflow_semantics(gate: dict) -> list[str]:
    prefix = "workflow tbr_gate"
    errors: list[str] = []
    _require_true(errors, prefix, gate, "required")
    if not _raw_query_policy_blocks_raw_sql(_value(gate, "translator.raw_query_policy")):
        errors.append(f"{prefix}: translator.raw_query_policy must forbid raw production SQL unless a semantic/policy gate mediates it")
    _require_exact(errors, prefix, gate, "bouncer.effective_permission_model", "user_via_agent")
    _require_true(errors, prefix, gate, "bouncer.task_scoped_tokens_required")
    _require_true(errors, prefix, gate, "recorder.run_card_required")
    errors.extend(_validate_trace_fields(prefix, _value(gate, "recorder.trace_fields")))
    for dotted in (
        "translator.canonical_definitions", "translator.source_of_truth_refs",
        "bouncer.policy_engine_ref", "recorder.retention_class",
        "recorder.tamper_evidence", "recorder.review_cadence",
    ):
        _require_concrete(errors, prefix, gate, dotted)
    _require_bounded(errors, prefix, gate, "bouncer.policy_engine_ref")
    return errors


def _validate_node_semantics(nid: str, gate: dict) -> list[str]:
    prefix = f"{nid}: tbr_gate"
    errors: list[str] = []
    _require_true(errors, prefix, gate, "required")
    _require_true(errors, prefix, gate, "translator.source_refs_required")
    _require_false(errors, prefix, gate, "translator.raw_query_allowed")
    _require_exact(errors, prefix, gate, "bouncer.human_identity_passthrough", "required")
    for dotted in (
        "bouncer.agent_identity", "bouncer.allowed_resources", "bouncer.effective_permission_path",
        "bouncer.task_scope", "bouncer.forbidden_resources",
    ):
        _require_bounded(errors, prefix, gate, dotted)
    _require_true(errors, prefix, gate, "recorder.run_card_required")
    _require_true(errors, prefix, gate, "recorder.permission_decision_logged")
    _require_true(errors, prefix, gate, "recorder.definition_refs_logged")
    _require_true(errors, prefix, gate, "recorder.source_refs_logged")
    errors.extend(_validate_trace_fields(prefix, _value(gate, "recorder.trace_fields")))
    for dotted in (
        "translator.definition_refs", "translator.source_of_truth_refs",
        "bouncer.agent_identity", "bouncer.allowed_resources", "bouncer.effective_permission_path",
        "bouncer.task_scope", "bouncer.forbidden_resources",
        "recorder.retention_class", "recorder.tamper_evidence",
    ):
        _require_concrete(errors, prefix, gate, dotted)
    return errors


def validate_workflow_tbr(workflow: dict) -> list[str]:
    if not tbr_required_for_workflow(workflow):
        return []
    gate = workflow.get("tbr_gate") or {}
    if not gate:
        return ["workflow tbr_gate missing (Translator/Bouncer/Recorder proof required)"]
    errors = _validate_block("workflow tbr_gate", gate, [
        "required",
        "translator.canonical_definitions",
        "translator.source_of_truth_refs",
        "translator.raw_query_policy",
        "bouncer.effective_permission_model",
        "bouncer.policy_engine_ref",
        "bouncer.task_scoped_tokens_required",
        "recorder.run_card_required",
        "recorder.trace_fields",
        "recorder.retention_class",
        "recorder.tamper_evidence",
        "recorder.review_cadence",
    ])
    errors.extend(_validate_workflow_semantics(gate))
    return errors


def validate_node_tbr(workflow: dict, node_card: dict) -> list[str]:
    nid = node_card.get("node_id", "<node>")
    if not tbr_required_for_node(workflow, node_card):
        return []
    gate = node_card.get("tbr_gate") or {}
    if not gate:
        return [f"{nid}: tbr_gate missing (Translator/Bouncer/Recorder proof required)"]
    errors = _validate_block(f"{nid}: tbr_gate", gate, [
        "required",
        "translator.definition_refs",
        "translator.source_of_truth_refs",
        "translator.source_refs_required",
        "translator.raw_query_allowed",
        "bouncer.agent_identity",
        "bouncer.human_identity_passthrough",
        "bouncer.allowed_resources",
        "bouncer.forbidden_resources",
        "bouncer.effective_permission_path",
        "bouncer.task_scope",
        "recorder.run_card_required",
        "recorder.trace_fields",
        "recorder.permission_decision_logged",
        "recorder.definition_refs_logged",
        "recorder.source_refs_logged",
        "recorder.retention_class",
        "recorder.tamper_evidence",
    ])
    errors.extend(_validate_node_semantics(nid, gate))
    return errors


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

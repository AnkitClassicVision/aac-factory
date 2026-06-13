# Translator / Bouncer / Recorder governance gate

This patch adds the Governance Layer for Agents pattern to AAC Factory without creating a new framework. TBR becomes a production-readiness proof gate inside the existing AAC card, QA, compiler, and run-card path.

## Why this exists

Enterprise agent rollouts usually break on trust rather than raw model capability:

- **Dirty or ambiguous data:** agents ask raw tables or invent metric semantics.
- **Over-broad access:** agents inherit shared service accounts or broad OAuth scopes.
- **No audit trail:** nobody can reconstruct what definition, source, permission, or output path produced an answer.

The TBR gate turns those failure modes into explicit proof fields:

- **Translator:** which canonical definition and source of truth answered this node?
- **Bouncer:** which user-via-agent permission path allowed this access?
- **Recorder:** can a run card prove prompt/response/tool/gate/source/definition/permission outcomes after the fact?

## Where it lives

Generated workflow cards now include `tbr_gate`:

```json
{
  "tbr_gate": {
    "required": true,
    "translator": {
      "canonical_definitions": [],
      "source_of_truth_refs": [],
      "raw_query_policy": "agents_may_not_query_raw_production_tables_without_a_semantic_or_policy_gate"
    },
    "bouncer": {
      "effective_permission_model": "user_via_agent",
      "policy_engine_ref": "...",
      "task_scoped_tokens_required": true
    },
    "recorder": {
      "run_card_required": true,
      "trace_fields": [
        "run_id", "workflow", "node_id", "runtime_mode", "input_ref", "output_ref",
        "prompt_ref", "prompt_version", "response_ref", "tool_call_refs", "user_ref", "recipient_ref",
        "source_refs", "definition_refs", "permission_decision", "gate_outcomes", "refuse",
        "external_actions_taken", "retention_class", "tamper_evidence"
      ],
      "retention_class": "...",
      "tamper_evidence": "..."
    }
  }
}
```

Generated node cards now include node-level `tbr_gate` with:

- `translator.definition_refs`
- `translator.source_of_truth_refs`
- `translator.source_refs_required`
- `translator.raw_query_allowed`
- `bouncer.agent_identity`
- `bouncer.human_identity_passthrough`
- `bouncer.allowed_resources`
- `bouncer.forbidden_resources`
- `bouncer.effective_permission_path`
- `bouncer.task_scope`
- `recorder.trace_fields`
- `recorder.permission_decision_logged`
- `recorder.definition_refs_logged`
- `recorder.source_refs_logged`
- `recorder.retention_class`
- `recorder.tamper_evidence`

Default scaffolds intentionally contain `TODO` fields. That is correct: the factory may scaffold the layer, but certification waits for source owners to fill the actual semantic definitions, policy refs, and recorder controls.

## Validation behavior

`validate_agent_package.py` now reports `tbr_blockers` and adds a `tbr` section to `exports/readiness_report.json`.

- Missing TBR blocks are blockers.
- TODO TBR fields count as TODO blockers.
- R0 can still pass because a scaffolded package is structurally valid.
- R1/R2 certification remains blocked until owners resolve the TBR proof fields.

## QA behavior

`qa_agent_package.py` adds three checks:

- `tbr_gate_present` — workflow and node TBR blocks exist.
- `tbr_recorder_trace_contract` — the recorder has the portable trace fields needed for audit proof.
- `tbr_gate_resolved` — semantic definitions, source refs, permission path, retention, and tamper-evidence are no longer TODO before certification.

Self-healing may inject missing default TBR scaffolds or restore recorder trace fields. It will not invent metric definitions, source-of-truth refs, access policies, or compliance retention. Those stay human/source-owner work.

## Runtime behavior

The S6 compiler carries node `tbr_gate` into `build/agent/nodes.json`.

Compiled runtimes now write TBR proof into each run card:

- `tbr_required`
- `tbr.definition_refs`
- `tbr.semantic_source_refs`
- `tbr.permission_decision`
- `tbr.prompt_ref`
- `tbr.prompt_version`
- `tbr.response_ref`
- `tbr.tool_call_refs`
- `tbr.user_ref`
- `tbr.recipient_ref`
- `tbr.trace_fields`
- `tbr.retention_class`
- `tbr.tamper_evidence`

If TBR proof is incomplete or still contains TODO fields, the run may execute in shadow/debug mode but is marked non-certifying through `certification_blockers` such as:

- `tbr_definition_refs_missing`
- `tbr_semantic_source_refs_missing`
- `tbr_prompt_ref_missing`
- `tbr_tool_call_refs_missing`
- `tbr_user_ref_missing`
- `tbr_permission_decision_incomplete`
- `tbr_trace_fields_incomplete`
- `tbr_retention_class_missing`
- `tbr_tamper_evidence_missing`
- `tbr_contains_todo`

This matches v0.3 Runtime Truth: claims are either physically enforced, explicitly non-certifying, or blocked before certification/promotion.

## Operator checklist

Before any agent touches real enterprise data:

- Translator
  - Fill canonical business terms and definitions.
  - Point each term to a source-of-truth ref, semantic layer, or owner-approved policy doc.
  - Confirm agents do not write raw production SQL without a semantic/policy gate.

- Bouncer
  - Name the agent identity.
  - Pass the human identity through as `user via agent`.
  - Define allowed resources/actions and forbidden resources/actions.
  - Put the runtime authorization policy/proxy in the `effective_permission_path`.
  - Use task-scoped, short-lived capabilities where the target system supports it.

- Recorder
  - Keep the required TBR trace fields in run cards.
  - Set retention class and tamper-evidence.
  - Confirm audit log access is itself access-controlled.

## What TBR does not do

TBR does not make the model smarter. It makes the agent's claims and access path provable enough to trust. It also does not replace source-side controls. A compiled AAC runtime still needs real API, warehouse, and MCP servers to enforce policy at the boundary.
#!/usr/bin/env python3
"""
Self-healing pass for an AAC Agent Package, driven by QA feedback.

Reads exports/qa_report.json and splits findings by fix class
(per the workflow card's automation_policy):

  AUTO (applied here, no human):
    stale_exports     -> re-run concept export + combined export
    derivable_fields  -> inject telemetry/supervision/automation_policy from convention
    missing_stubs     -> create prompt stubs for C/A nodes
    control_state_sync-> sync CONTROL_STATE statuses to reality

  PROPOSAL (queued for the human-over-the-loop review queue, never auto-applied):
    meaning changes, runtime assignment, golden grades, leak findings, TODO fills.
    Written to exports/repair_proposals.json with a proposed action each.

One pass per invocation; run_pipeline.py loops heal->validate->qa until stable (max per policy).
Never touches golden grades. Never flips a human gate. Never edits atlas meaning.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from concept_to_process import AUTOMATION_POLICY_DEFAULT, execution_block, objective_block, supervision_block
from tbr_gate import TBR_TRACE_FIELDS, default_node_tbr_gate, default_workflow_tbr_gate

PROPOSED_ACTIONS = {
    "spine_locked": "Human: lock the spine in atlas/atlas.json (one falsifiable claim, 3-9 trunk nodes).",
    "atlas_nodes_tagged": "Human/agent at grill: tag confidence, provenance, status on the listed ATLAS nodes.",
    "lint_clean": "Re-run scripts/atlas_to_concept.py after fixing atlas.json; the generator refuses with exact fixes.",
    "crosswalk_complete": "Re-run scripts/atlas_to_concept.py (regenerates crosswalk from atlas).",
    "workflow_required_fields": "Fill the missing workflow-card fields from grill answers or live probes.",
    "graph_has_refusal_paths": "Add refuse_sink / hard_refuse_sink edges: refusal is first-class, never optional.",
    "cards_exist_per_node": "Re-run scripts/concept_to_process.py to generate missing node cards.",
    "card_items_complete": "Fill TODO card fields from grill answers or measured probes (see readiness_report blockers).",
    "tbr_gate_present": "Re-run concept_to_process.py or let self-heal inject TBR defaults, then fill definitions/policies from owners.",
    "tbr_recorder_trace_contract": "Restore the Recorder trace-field contract; run cards must prove definitions, source refs, permission decisions, gates, refusals, and actions.",
    "tbr_gate_resolved": "Human/source-owner queue: fill semantic definitions, source-of-truth refs, permission path, and recorder retention/tamper evidence.",
    "h_nodes_justified": "Human decision: justify each inline human gate as highest-risk, or demote to human_over_loop.",
    "golden_set_policy": "Human-over-loop queue: harvest/grade golden examples (grade, never author).",
    "no_leaked_identifiers": "BLOCKING: redact the matched strings, then re-run QA. Never auto-edited.",
}


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(p: Path, data) -> None:
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _complete_trace_fields(existing) -> list[str]:
    fields = list(existing or [])
    for field in TBR_TRACE_FIELDS:
        if field not in fields:
            fields.append(field)
    return fields


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/heal_agent_package.py <package_dir>")
    pkg = Path(sys.argv[1]).resolve()
    report = load(pkg / "exports" / "qa_report.json")
    if not report:
        raise SystemExit("No exports/qa_report.json — run qa_agent_package.py first.")

    wf_path = pkg / "process" / "workflow.aac.json"
    wf = load(wf_path, {}) or {}
    policy = (wf.get("automation_policy") or AUTOMATION_POLICY_DEFAULT)["self_healing"]
    if not policy.get("enabled", True):
        print("Self-healing disabled by automation_policy — proposals only.")
    auto_classes = set(policy.get("auto_fix_classes", []))

    applied: list[str] = []
    proposals: list[dict] = []

    for s in report.get("scenarios", []):
        if s["result"] in {"PASS", "PENDING_LLM"}:
            continue
        fix_class = s.get("fix_class") or ""
        if s.get("auto_fixable") and fix_class in auto_classes and policy.get("enabled", True):
            if fix_class == "stale_exports":
                for d in sorted(pkg.glob("*_concept_map")):
                    subprocess.run([sys.executable, "scripts/export_map.py", "default"],
                                   cwd=d, capture_output=True, text=True)
                applied.append(f"{s['id']}: re-exported concept map")
            elif fix_class == "missing_stubs":
                for nid_card in (pkg / "process" / "nodes").glob("*.aac.json"):
                    card = load(nid_card, {})
                    pref = card.get("prompt_ref")
                    if card.get("runtime_mode") in {"C", "A"} and pref and not (pkg / pref).exists():
                        (pkg / pref).parent.mkdir(parents=True, exist_ok=True)
                        (pkg / pref).write_text(
                            f"# Prompt — {card.get('node_id')} (v{card.get('prompt_version', '0.1.0')})\n\n"
                            f"Purpose: {card.get('purpose', 'TODO')}\n\nTODO: bounded actions, grounding, "
                            "output contract, refuse rules.\n", encoding="utf-8")
                        applied.append(f"{s['id']}: created {pref}")
            elif fix_class == "derivable_fields":
                if s["id"] == "automation_policy_present" and wf:
                    changed_wf = False
                    if not wf.get("automation_policy"):
                        wf["automation_policy"] = json.loads(json.dumps(AUTOMATION_POLICY_DEFAULT))
                        applied.append("automation_policy: injected human-over-the-loop default")
                        changed_wf = True
                    if not wf.get("runtime_target"):
                        wf["runtime_target"] = "TODO: compile target — vps-scheduler | lambda | hermes-cron (owner picks at S6)"
                        applied.append("runtime_target: injected TODO for S6 compile choice")
                        changed_wf = True
                    if changed_wf:
                        save(wf_path, wf)
                if s["id"] in {"tbr_gate_present", "tbr_recorder_trace_contract"}:
                    changed_wf = False
                    if not wf.get("tbr_gate"):
                        wf["tbr_gate"] = default_workflow_tbr_gate(wf)
                        changed_wf = True
                    else:
                        rec = wf.setdefault("tbr_gate", {}).setdefault("recorder", {})
                        complete_fields = _complete_trace_fields(rec.get("trace_fields"))
                        if complete_fields != list(rec.get("trace_fields") or []):
                            rec["trace_fields"] = complete_fields
                            changed_wf = True
                    if changed_wf:
                        save(wf_path, wf)
                        applied.append(f"{s['id']}: healed workflow tbr_gate")
                    for nid_card in sorted((pkg / "process" / "nodes").glob("*.aac.json")):
                        card = load(nid_card, {}) or {}
                        changed = False
                        if not card.get("tbr_gate"):
                            card["tbr_gate"] = default_node_tbr_gate(wf, card)
                            changed = True
                        else:
                            rec = card.setdefault("tbr_gate", {}).setdefault("recorder", {})
                            complete_fields = _complete_trace_fields(rec.get("trace_fields"))
                            if complete_fields != list(rec.get("trace_fields") or []):
                                rec["trace_fields"] = complete_fields
                                changed = True
                        if changed:
                            save(nid_card, card)
                            applied.append(f"{s['id']}: healed {nid_card.name}")
                if s["id"] == "objective_present":
                    gref = wf.get("golden_set_ref", f"process/evals/{pkg.name}.golden.json")
                    for nid_card in sorted((pkg / "process" / "nodes").glob("*.aac.json")):
                        card = load(nid_card, {}) or {}
                        obj = card.get("objective") or {}
                        if card.get("runtime_mode") in {"C", "A"} and not (
                                (obj.get("primary") or {}).get("metric") and obj.get("improvement_policy")):
                            card["objective"] = objective_block(card.get("node_id", nid_card.stem),
                                                                card.get("eval_ref", gref))
                            save(nid_card, card)
                            applied.append(f"objective_present: healed {nid_card.name}")
                if s["id"] in {"telemetry_per_node", "supervision_over_loop", "execution_declared"}:
                    metrics_default = (wf.get("observability", {}) or {}).get("metrics") or ["runs", "refusals"]
                    for nid_card in sorted((pkg / "process" / "nodes").glob("*.aac.json")):
                        card = load(nid_card, {}) or {}
                        changed = False
                        t = card.get("telemetry") or {}
                        if not (t.get("run_card_required") and t.get("metrics") and t.get("artifact_path")):
                            card["telemetry"] = {
                                "run_card_required": True,
                                "artifact_path": f"process/run-cards/{card.get('node_id')}/",
                                "metrics": t.get("metrics") or metrics_default,
                            }
                            changed = True
                        rm = card.get("runtime_mode")
                        sup = card.get("supervision") or {}
                        needs_sup = (rm in {"C", "A"} and (sup.get("mode") != "human_over_loop"
                                     or sup.get("inline_approval") is not False)) or (rm in {"D", "H"} and not sup)
                        if needs_sup:
                            card["supervision"] = supervision_block(rm or "D", str(card.get("purpose", "")))
                            changed = True
                        if not card.get("execution"):
                            card["execution"] = execution_block(rm or "D")
                            changed = True
                        if changed:
                            save(nid_card, card)
                            applied.append(f"{s['id']}: healed {nid_card.name}")
        else:
            proposals.append({
                "finding": s["id"], "severity": s["weight"], "evidence": s["evidence"],
                "proposed_action": PROPOSED_ACTIONS.get(s["id"], "Human review: see qa_report.json."),
                "owner": "human_over_loop_queue",
            })

    # control_state_sync runs every pass (always derivable)
    cs_path = pkg / "CONTROL_STATE.json"
    cs = load(cs_path)
    if cs is not None:
        cs.setdefault("qa", {})["report"] = "exports/qa_report.json"
        cs["qa"]["verdict"] = report.get("verdict")
        cs["qa"]["satisfaction"] = report.get("satisfaction")
        cs.setdefault("healing", {})["last_applied"] = applied
        cs["healing"]["open_proposals"] = len(proposals)
        save(cs_path, cs)

    save(pkg / "exports" / "repair_proposals.json",
         {"package": pkg.name, "auto_applied": applied, "proposals": proposals,
          "note": "AUTO fixes are derivable-by-convention only. Proposals wait in the human-over-the-loop queue."})

    print(f"Healing pass: {len(applied)} auto-fix(es) applied, {len(proposals)} proposal(s) queued.")
    for a in applied[:8]:
        print(f"  fixed: {a}")
    for p in proposals[:8]:
        print(f"  queued [{p['severity']}] {p['finding']}: {p['proposed_action'][:80]}")


if __name__ == "__main__":
    main()

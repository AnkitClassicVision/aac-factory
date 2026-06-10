#!/usr/bin/env python3
"""
Derive the AAC process layer (directed graph + cards) from the concept map + ATLAS.

Reads  <package>/atlas/atlas.json, <package>/atlas/crosswalk.json,
       <package>/<slug>_concept_map/knowledge/exports/latest.rivermap.json,
       <package>/process/source_packet.aac.json   (optional: interview packet / draft workflow card)
Writes <package>/process/workflow.aac.json        (AAC 2.5 workflow card, provenance-tagged)
       <package>/process/nodes/<node_id>.aac.json (one AAC node card per node)
       <package>/process/prompts/<node_id>.md     (prompt stubs for C nodes)
       <package>/process/evals/<slug>.golden.json (golden set, never overwritten once graded)
       crosswalk.process_nodes                    (process node -> concept/atlas traceability)

Never overwrites an existing workflow.aac.json or node card unless --force
(human edits beat regeneration). TODO fields carry provenance "assumed" —
the validator counts them as blockers.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from factory_config import load_config

CFG = load_config()

WORKFLOW_REQUIRED = [
    "card_type", "aac_card_version", "workflow_id", "workflow_box", "control_topology",
    "value_mode", "cost_framing", "trigger", "finished_state", "sinks", "owners",
    "max_lane", "nodes", "agents", "hard_refuse", "observability", "residue", "escalation_path",
]
SINK_IDS = {"happy_sink", "refuse_sink", "hard_refuse_sink"}


def tokens(s: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "of", "to", "for", "with", "in", "on", "per", "set"}
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in stop}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def todo(field: str) -> str:
    return f"TODO: {field} (fill from grill answer or live data probe)"


# AAC Step 2 decision support: the right runtime per node, checked in priority order.
# A before H before C before D: irreversible beats human-accountability beats judgment beats rules.
RUNTIME_SIGNALS = [
    ("A", ("send", "pay", "payment", "delete", "publish", "enroll", "merge", "deploy"),
     "touches an irreversible or high-blast action: AI may prepare, a human approves (AAC Step 2 hard downgrade)"),
    ("H", ("approve", "review", "grade", "sign", "negotiate", "interview"),
     "human judgment / accountability step (regulatory, contractual, or trust)"),
    ("C", ("classify", "label", "draft", "summarize", "decide", "recommend", "score", "judge",
           "disposition", "extract", "rank"),
     "bounded judgment on structured input: closed-loop AI with the five disciplines"),
    ("D", ("pull", "fetch", "dedupe", "snapshot", "split", "route", "log", "validate", "parse",
           "sync", "count", "queue", "filter", "ingest"),
     "rule-expressible with deterministic input: cheapest runtime that satisfies the attributes"),
]


def suggest_runtime(node_id: str, purpose: str) -> dict | None:
    """Propose D/C/A/H for a node from its language. Suggestion only: the human assigns at the grill."""
    text = f"{node_id} {purpose}".lower()
    for mode, keywords, reason in RUNTIME_SIGNALS:
        hit = next((k for k in keywords if re.search(rf"\b{k}\w*", text)), None)
        if hit:
            return {"mode": mode, "signal": hit, "reason": reason,
                    "decided_by": "suggestion only — human assigns runtime_mode at the grill (AAC Step 2)"}
    return None


HIGH_RISK_HINTS = ("approve", "send", "sign", "residue", "promot", "irreversib", "pay", "delete",
                   "publish", "external", "hire", "fire", "phi", "review")

AUTOMATION_POLICY_DEFAULT = {
    "stance": "human_over_loop",
    "auto_advance": {"R0": True, "R1": True, "R2": "auto_when_golden_graded", "R3": False, "R4": False},
    "human_gates_only": [
        "golden grading (async review queue, never inline per run)",
        "residue statement signing",
        "lane promotion to R3/R4",
        "any irreversible or external action (send/write/pay/delete)",
    ],
    "self_healing": {
        "enabled": True,
        "auto_fix_classes": ["derivable_fields", "stale_exports", "missing_stubs", "control_state_sync"],
        "proposal_only_classes": ["meaning_changes", "runtime_assignment", "golden_grades", "leak_findings"],
        "max_iterations": 3,
    },
}


def objective_block(node_id: str, eval_ref: str) -> dict:
    """Karpathy-style measurable objective per judgment node: the improvement loop
    optimizes against this, never against vibes. Holdout stays sealed for the gate."""
    return {
        "primary": {"metric": "golden_accuracy", "target": 0.9, "eval_ref": eval_ref},
        "guardrails": [{"metric": "hard_refuse_violations", "max": 0},
                       {"metric": "leak_findings", "max": 0}],
        "cost": {"metric": "model_cost_rank", "direction": "minimize"},
        "latency": {"metric": "p95_seconds", "budget": None},
        "improvement_policy": {
            "auto_adopt": "cheaper_or_better_with_holdout_pass",
            "holdout_ref": f".holdout/evals/{node_id}.holdout.json",
            "re_enter_gates": True,
        },
    }


def execution_block(runtime: str) -> dict:
    """Harness choice per node: WHAT executes it and WHERE. Defaults come from
    factory.config.json (user preference); owners may override per node card."""
    if runtime in {"C", "A"}:
        ep = CFG.get("execution_preferences", {})
        options = ["anthropic-api", "claude-code-headless", "hermes-skill"]
        if CFG.get("model_preferences", {}).get("allow_local_models"):
            options.append("local-model")
        return {
            "harness": ep.get("judgment_harness", "anthropic-api"),
            "auth_mode": ep.get("auth_mode", "api-key"),
            "harness_options": options,
            "runs_on": "inherits workflow.runtime_target",
            "decided_by": "factory.config.json preference — owner override allowed per node; the model field picks the model",
        }
    if runtime == "H":
        return {"harness": "human-queue",
                "runs_on": "review-queue surface chosen at compile (Monday / Slack / email)"}
    return {
        "harness": CFG.get("execution_preferences", {}).get("deterministic_harness", "python-deterministic"),
        "harness_options": ["python-deterministic", "lambda"],
        "runs_on": "inherits workflow.runtime_target",
    }


def supervision_block(runtime: str, purpose: str) -> dict:
    """Human OVER the loop: C/A run autonomously under queues + sampling + escalation;
    D runs on telemetry; inline human gates only with a high-risk justification."""
    if runtime in {"C", "A"}:
        return {
            "mode": "human_over_loop",
            "inline_approval": False,
            "review_queue": "process/run-cards/_review_queue/",
            "sampling_rate": "10% weekly (raise after any escalation)",
            "escalation_triggers": ["confidence below floor", "hard_refuse event",
                                    "QA verdict block", "drift monitor alarm"],
        }
    if runtime == "H":
        hit = next((k for k in HIGH_RISK_HINTS if k in purpose.lower()), None)
        return {
            "mode": "inline_human_gate",
            "justification": (f"highest-risk class detected ({hit}): inline human stays"
                              if hit else todo("justify inline human gate or demote to human_over_loop")),
        }
    return {"mode": "automated", "oversight": "run cards + weekly 10-record spot-check"}


def normalize_nodes(raw_nodes) -> list[dict]:
    out = []
    for n in raw_nodes or []:
        if isinstance(n, str):
            out.append({"node_id": n, "runtime_mode": "TODO", "purpose": todo("purpose")})
        else:
            out.append({
                "node_id": n.get("node_id") or todo("node_id"),
                "runtime_mode": n.get("runtime_mode", "TODO"),
                "purpose": n.get("purpose", todo("purpose")),
                **{k: v for k, v in n.items() if k not in {"node_id", "runtime_mode", "purpose"}},
            })
    return out


def build_workflow_card(slug: str, atlas: dict, source: dict | None) -> tuple[dict, list[str]]:
    notes: list[str] = []
    card = dict(source) if source else {}
    card["card_type"] = "workflow"
    card["aac_card_version"] = card.get("aac_card_version", "2.5-draft")
    card.setdefault("workflow_id", slug)
    card.setdefault("workflow_box", atlas.get("spine") if not str(atlas.get("spine", "")).startswith("TODO") else todo("workflow_box"))
    card.setdefault("control_topology", "graph_directed")
    card.setdefault("value_mode", "augment")
    card.setdefault("trigger", todo("trigger"))
    card.setdefault("finished_state", todo("finished_state"))
    card.setdefault("sinks", {"happy": todo("happy sink"), "refuse": todo("refuse sink"),
                              "hard_refuse": todo("hard refuse sink")})
    card.setdefault("owners", {"process_owner": todo("process_owner"), "technical_owner": todo("technical_owner"),
                               "reviewer": todo("reviewer"), "residue_accepter": todo("residue_accepter")})
    card.setdefault("max_lane", "design_scaffold")
    card.setdefault("pilot_ring", "R0")
    card.setdefault("director", "deterministic_router")
    card.setdefault("agents", [f"{slug}-agent"])
    card.setdefault("hard_refuse", [todo("hard_refuse classes (data/actions the agent must never touch)")])
    card.setdefault("observability", {"run_card_required": True, "review_cadence": todo("review cadence"),
                                      "metrics": []})
    card.setdefault("residue", {"statement": todo("residue statement: what gates cannot catch"),
                                "accepter": card.get("owners", {}).get("residue_accepter", todo("accepter")),
                                "signed": False})
    card.setdefault("escalation_path", todo("escalation_path"))
    card.setdefault("cost_framing", {"cost_of_failure": todo("cost_of_failure"),
                                     "cost_of_inaction": todo("cost_of_inaction"),
                                     "per_error_cost_band": todo("per_error_cost_band")})
    card.setdefault("lane_promotion", {
        "target_lane": todo("target lane"),
        "condition": todo("promotion condition (e.g., N consecutive approvals, zero edits)"),
        "counter_reset_on": ["any edit", "any hard-refuse violation"],
        "post_promotion_qa": todo("post-promotion QA"),
        "evidence_ref": "process/run-cards/",
    })
    # Human OVER the loop: the system runs; humans supervise via queues, sampling, and
    # escalation. Inline human gates exist ONLY for the highest-risk classes.
    card.setdefault("automation_policy", json.loads(json.dumps(AUTOMATION_POLICY_DEFAULT)))
    preferred_target = CFG.get("deploy_preferences", {}).get("runtime_target")
    card.setdefault("runtime_target", preferred_target or
                    "TODO: compile target — vps-scheduler | lambda | hermes-cron (owner picks at S6)")

    # nodes: from source packet, else from ATLAS spine
    nodes = normalize_nodes(card.get("nodes"))
    if not nodes:
        by_id = {n["id"]: n for n in atlas.get("nodes", [])}
        for i, sid in enumerate(atlas.get("spine_path", []), start=1):
            nm = by_id.get(sid, {})
            nodes.append({
                "node_id": f"{slug}-s{i}-{re.sub(r'[^a-z0-9]+', '-', nm.get('name', f'stage{i}').lower()).strip('-')[:28]}",
                "runtime_mode": "TODO",
                "purpose": nm.get("short_def") or nm.get("name") or todo("purpose"),
                "atlas_ref": sid,
            })
        notes.append("nodes derived from ATLAS spine_path — confirm in S4 VERIFY before filling cards")
    card["nodes"] = nodes

    edges = card.get("edges") or []
    if not edges:
        for a, b in zip(nodes, nodes[1:]):
            edges.append({"from": a["node_id"], "to": b["node_id"], "condition": "TODO: condition"})
        if nodes:
            edges.append({"from": nodes[-1]["node_id"], "to": "happy_sink", "condition": "TODO: success condition"})
            edges.append({"from": nodes[0]["node_id"], "to": "refuse_sink", "condition": "TODO: refuse condition"})
            edges.append({"from": nodes[0]["node_id"], "to": "hard_refuse_sink", "condition": "TODO: hard-refuse condition"})
        notes.append("edges scaffolded as a linear chain + sinks — replace with real conditional routing")
    card["edges"] = edges

    card.setdefault("golden_set_ref", f"process/evals/{slug}.golden.json")
    return card, notes


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    if not args:
        raise SystemExit("Usage: python scripts/concept_to_process.py <package_dir> [--force]")
    pkg = Path(args[0]).resolve()
    slug = pkg.name

    atlas_path = pkg / "atlas" / "atlas.json"
    cw_path = pkg / "atlas" / "crosswalk.json"
    map_dirs = sorted(pkg.glob("*_concept_map"))
    if not (atlas_path.exists() and cw_path.exists() and map_dirs):
        raise SystemExit("Run scripts/atlas_to_concept.py first (need atlas.json, crosswalk.json, concept map).")
    rivermap_path = map_dirs[0] / "knowledge" / "exports" / "latest.rivermap.json"
    if not rivermap_path.exists():
        raise SystemExit(f"Missing concept export {rivermap_path} — run lint + export first.")

    atlas = load_json(atlas_path)
    crosswalk = load_json(cw_path)
    rivermap = load_json(rivermap_path)
    concept_by_id = {n["id"]: n for n in rivermap.get("nodes", [])}

    source_path = pkg / "process" / "source_packet.aac.json"
    source = load_json(source_path) if source_path.exists() else None

    wf_path = pkg / "process" / "workflow.aac.json"
    if wf_path.exists() and not force:
        card = load_json(wf_path)
        notes = ["workflow.aac.json already exists — left untouched (use --force to regenerate)"]
    else:
        card, notes = build_workflow_card(slug, atlas, source)
        card["concept_map_ref"] = str(rivermap_path.relative_to(pkg))
        card["atlas_ref"] = "atlas/atlas.json"
        prov = card.get("provenance") or {}
        for f in WORKFLOW_REQUIRED:
            if f not in prov:
                val = json.dumps(card.get(f), ensure_ascii=False)
                prov[f] = {"source": "assumed" if "TODO" in val else ("prefilled" if source else "assumed"),
                           "ref": "concept_to_process.py generator" + (" + source_packet" if source else "")}
        if source and isinstance(source.get("provenance"), dict):
            prov.update(source["provenance"])
        card["provenance"] = prov
        write_json(wf_path, card)

    # concept_ref resolution per process node
    process_map = atlas.get("process_map") or {}
    atlas_to_concept = {aid: m.get("concept_node_id") for aid, m in (crosswalk.get("atlas_nodes") or {}).items()}
    concept_label_tokens = {cid: tokens(n.get("label", "") + " " + (n.get("short_def") or ""))
                            for cid, n in concept_by_id.items()}

    nodes = normalize_nodes(card.get("nodes"))
    flags: list[str] = []
    cw_process: dict[str, dict] = {}
    nodes_dir = pkg / "process" / "nodes"
    prompts_dir = pkg / "process" / "prompts"

    for n in nodes:
        node_id = n["node_id"]
        atlas_ref = process_map.get(node_id) or n.get("atlas_ref")
        concept_ref = atlas_to_concept.get(atlas_ref) if atlas_ref else None
        match_basis = "explicit process_map" if atlas_ref else None
        if concept_ref is None:
            nt = tokens(node_id + " " + str(n.get("purpose", "")))
            scored = sorted(((jaccard(nt, ct), cid) for cid, ct in concept_label_tokens.items()),
                            key=lambda x: (-x[0], x[1]))
            if scored and scored[0][0] >= 0.15:
                concept_ref, match_basis = scored[0][1], f"token match ({scored[0][0]:.2f})"
            else:
                concept_ref, match_basis = "TODO: map concept_ref (add node to atlas.process_map)", "unmapped"
                flags.append(f"{node_id}: no concept_ref — add to atlas.json process_map")

        runtime = n.get("runtime_mode", "TODO")
        card_path = nodes_dir / f"{node_id}.aac.json"
        cw_process[node_id] = {
            "concept_node_id": concept_ref,
            "concept_label": concept_by_id.get(concept_ref, {}).get("label") if concept_ref in concept_by_id else None,
            "atlas_node_id": atlas_ref,
            "match_basis": match_basis,
            "card": str(card_path.relative_to(pkg)),
        }
        if card_path.exists() and not force:
            continue

        rt_suggestion = None if runtime in {"D", "C", "A", "H"} else suggest_runtime(node_id, str(n.get("purpose", "")))
        is_ai = runtime in {"C", "A"}
        node_card = {
            "card_type": "node",
            "aac_card_version": "2.5-draft",
            "workflow": card.get("workflow_id", slug),
            "node_id": node_id,
            "purpose": n.get("purpose", todo("purpose")),
            "runtime_mode": runtime if runtime in {"D", "C", "A", "H"} else "TODO",
            "max_lane": card.get("max_lane", "design_scaffold"),
            "owner": card.get("owners", {}).get("process_owner", todo("owner")),
            "deliverable": n.get("purpose", todo("deliverable: what this node must produce")),
            "artifact": f"process/run-cards/{node_id}/",
            "concept_ref": concept_ref,
            "atlas_ref": atlas_ref or "TODO: atlas node id",
            "input_contract": {"schema": todo("input class / schema"), "source": todo("system of record")},
            "output_contract": {"schema": todo("output class / schema"),
                                "must_cite_sources": is_ai,
                                "artifact_path": f"process/run-cards/{node_id}/"},
            "data_classification": card.get("data_classification", todo("data classification")),
            "disciplines": {
                "bounded": {"summary": todo("finite action set / vocabulary") if is_ai else "deterministic rule — document the rule"},
                "grounded": {"summary": todo("source set + source-id requirement") if is_ai else "inputs traced to system of record"},
                "gated": {"summary": todo("confidence floor by action class + gates") if is_ai else "schema/validation gate"},
                "observed": {"summary": todo("telemetry + drift monitors") if is_ai else "run-card logging"},
                "governed": {"summary": todo("refuse path + kill switch + versioned thresholds") if is_ai else "kill switch + change control"},
            },
            "gates": {
                "input": {"checks": [todo("input gate: reject bad data before run")]},
                "output": {"checks": [todo("output gate: schema/grounding/confidence")],
                           **({"confidence_floor": todo("0.6 PURE-QUERY / 0.8 MUTATE / 0.92 IRREVERSIBLE")} if is_ai else {})},
                "cross_check": {"checks": [todo("cross-check policy")]} if is_ai else {"checks": ["n/a (deterministic)"]},
                "action": {"checks": [todo("idempotency / rate / blast-radius checks")]},
            },
            "hard_refuse": card.get("hard_refuse", [todo("hard refuse classes")]),
            "telemetry": {
                "run_card_required": True,
                "artifact_path": f"process/run-cards/{node_id}/",
                "metrics": [m for m in (card.get("observability", {}).get("metrics") or [])] or [todo("metrics")],
            },
            "kill_switch": card.get("kill_switch", todo("kill switch")),
            "supervision": supervision_block(runtime, str(n.get("purpose", ""))),
            "execution": execution_block(runtime),
            "provenance_note": "generator stubs are provenance=assumed until confirmed/measured",
        }
        if rt_suggestion:
            node_card["runtime_suggestion"] = rt_suggestion
            flags.append(f"{node_id}: runtime_mode TODO — suggested {rt_suggestion['mode']} "
                         f"(signal: {rt_suggestion['signal']}); confirm at grill")
        if is_ai:
            node_card.update({
                "model": (CFG.get("model_preferences", {}).get("default_judgment_model")
                          or todo("model id (re-calibrate confidence floors on model/prompt change)")),
                "prompt_ref": f"process/prompts/{node_id}.md",
                "prompt_version": "0.1.0",
                "eval_ref": card.get("golden_set_ref", f"process/evals/{slug}.golden.json"),
                "token_budget": {"per_run_max": 4000, "provenance": "assumed default"},
                "objective": objective_block(node_id, card.get("golden_set_ref",
                                             f"process/evals/{slug}.golden.json")),
            })
            prompt_path = prompts_dir / f"{node_id}.md"
            if not prompt_path.exists():
                prompt_path.parent.mkdir(parents=True, exist_ok=True)
                prompt_path.write_text(
                    f"# Prompt — {node_id} (v0.1.0)\n\n"
                    f"Purpose: {n.get('purpose', 'TODO')}\n\n"
                    f"Concept anchor: {cw_process[node_id].get('concept_label') or concept_ref}\n"
                    f"Spine: {atlas.get('spine', '')}\n\n"
                    "## Bounded action set\nTODO: finite vocabulary the model may choose from.\n\n"
                    "## Grounding\nTODO: sources the output must cite (source ids required).\n\n"
                    "## Output contract\nTODO: schema (must match the node card output_contract).\n\n"
                    "## Refuse\nIf input is out-of-distribution, ambiguous, or violates hard-refuse classes: refuse with reason.\n",
                    encoding="utf-8",
                )
        write_json(card_path, node_card)

    golden_path = pkg / "process" / "evals" / f"{slug}.golden.json"
    if not golden_path.exists():
        golden = (source or {}).get("golden_set") or card.get("golden_set") or []
        write_json(golden_path, golden)
    else:
        print(f"Golden set exists — untouched: {golden_path}")

    crosswalk["process_nodes"] = cw_process
    write_json(cw_path, crosswalk)

    cs_path = pkg / "CONTROL_STATE.json"
    if cs_path.exists():
        cs = load_json(cs_path)
        cs.setdefault("process", {})["status"] = "CARDS_GENERATED_TODOS_PENDING"
        old = [f for f in cs.get("flags", []) if not str(f).startswith("[concept_to_process]")]
        cs["flags"] = old + [f"[concept_to_process] {f}" for f in flags + notes]
        cs_path.write_text(json.dumps(cs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[OK] Process layer written: workflow card + {len(nodes)} node cards.")
    for x in notes + flags:
        print(f"  - {x}")


if __name__ == "__main__":
    main()

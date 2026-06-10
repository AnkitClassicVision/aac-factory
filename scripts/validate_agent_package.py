#!/usr/bin/env python3
"""
Validate an AAC Agent Package across all three layers and emit a readiness report.

Checks
  ATLAS    : spine locked, node tagging complete, validation backlog present for non-Evidenced nodes
  Concept  : lint passes (package's own lint_db.py), exports exist and are fresh
  Crosswalk: every ATLAS node mapped; every process node traces to concept + atlas; trunk coverage
  Process  : workflow card required fields, graph connectivity (entry -> sinks), node cards complete,
             C nodes carry model/prompt_ref/eval_ref, golden set harvested AND human-graded,
             TODO/assumed-provenance counts, owner-concentration warning
  Ladder   : R0 design_scaffold -> R1 read/recommend -> R2 draft -> R3/R4 (human gates, never auto)

Output: <package>/exports/readiness_report.json (+ console summary)
Exit   : 0 always in report mode; --strict exits 1 if R0 fails.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WORKFLOW_REQUIRED = [
    "card_type", "aac_card_version", "workflow_id", "workflow_box", "control_topology",
    "value_mode", "cost_framing", "trigger", "finished_state", "sinks", "owners",
    "max_lane", "nodes", "agents", "hard_refuse", "observability", "residue", "escalation_path",
]
NODE_REQUIRED = [
    "card_type", "aac_card_version", "workflow", "node_id", "runtime_mode", "max_lane", "owner",
    "input_contract", "output_contract", "data_classification", "disciplines", "gates",
    "hard_refuse", "telemetry", "kill_switch", "concept_ref", "atlas_ref", "deliverable", "artifact",
]
C_NODE_REQUIRED = ["model", "prompt_ref", "prompt_version", "eval_ref", "token_budget"]
SINK_IDS = {"happy_sink", "refuse_sink", "hard_refuse_sink"}


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def has_todo(value) -> bool:
    return "TODO" in json.dumps(value, ensure_ascii=False)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    if not args:
        raise SystemExit("Usage: python scripts/validate_agent_package.py <package_dir> [--strict]")
    pkg = Path(args[0]).resolve()
    blockers: list[str] = []
    warnings: list[str] = []
    human_gates: list[str] = []

    # ---------- ATLAS ----------
    atlas_path = pkg / "atlas" / "atlas.json"
    atlas_ok = atlas_path.exists()
    atlas = load(atlas_path) if atlas_ok else {}
    if not atlas_ok:
        blockers.append("atlas/atlas.json missing")
    else:
        spine = str(atlas.get("spine", ""))
        if not spine or spine.startswith("TODO"):
            blockers.append("ATLAS spine not locked (TODO)")
        if not 3 <= len(atlas.get("spine_path") or []) <= 9:
            blockers.append("ATLAS spine_path must have 3-9 nodes")
        conf_counts = {"Hunch": 0, "Reasoned": 0, "Evidenced": 0}
        for n in atlas.get("nodes", []):
            conf_counts[n.get("confidence", "Hunch")] = conf_counts.get(n.get("confidence", "Hunch"), 0) + 1
            for field in ("confidence", "provenance", "status"):
                if not n.get(field):
                    blockers.append(f"ATLAS node {n.get('id')}: missing {field}")
            if not n.get("proof_slot") and n.get("confidence") in ("Reasoned", "Evidenced"):
                warnings.append(f"ATLAS node {n.get('id')}: {n.get('confidence')} without a proof_slot")
        non_evidenced = conf_counts["Hunch"] + conf_counts["Reasoned"]
        if non_evidenced and not atlas.get("validation_backlog"):
            warnings.append(f"{non_evidenced} non-Evidenced ATLAS nodes but validation_backlog is empty")

    # ---------- Concept map ----------
    map_dirs = sorted(pkg.glob("*_concept_map"))
    lint_pass = False
    rivermap = {}
    if not map_dirs:
        blockers.append("concept map package missing (*_concept_map)")
    else:
        map_dir = map_dirs[0]
        r = subprocess.run([sys.executable, "scripts/lint_db.py"], cwd=map_dir,
                           capture_output=True, text=True)
        lint_pass = r.returncode == 0
        if not lint_pass:
            blockers.append(f"concept map lint FAILED:\n{r.stdout.strip()[:800]}")
        export = map_dir / "knowledge" / "exports" / "latest.rivermap.json"
        if not export.exists():
            blockers.append("concept map export missing — run export_map.py")
        else:
            rivermap = load(export)
            db = map_dir / "knowledge" / "knowledge.db"
            if db.exists() and db.stat().st_mtime > export.stat().st_mtime + 1:
                warnings.append("concept export older than knowledge.db — re-export")

    concept_ids = {n["id"] for n in rivermap.get("nodes", [])}
    trunk_concept_ids = {e["to"] for e in rivermap.get("edges", []) if e.get("is_trunk")} | \
                        {e["from"] for e in rivermap.get("edges", []) if e.get("is_trunk")}
    trunk_concept_ids.discard(rivermap.get("root"))

    # ---------- Crosswalk ----------
    cw_path = pkg / "atlas" / "crosswalk.json"
    crosswalk = load(cw_path) if cw_path.exists() else {}
    if not cw_path.exists():
        blockers.append("atlas/crosswalk.json missing — run atlas_to_concept.py")
    else:
        for aid in (n.get("id") for n in atlas.get("nodes", [])):
            if aid and aid not in (crosswalk.get("atlas_nodes") or {}):
                warnings.append(f"ATLAS node {aid} not mapped into the concept map")

    # ---------- Process ----------
    wf_path = pkg / "process" / "workflow.aac.json"
    todo_count = 0
    assumed_fields: list[str] = []
    graded = pending = 0
    ai_nodes: list[str] = []
    if not wf_path.exists():
        blockers.append("process/workflow.aac.json missing — run concept_to_process.py")
        wf = {}
    else:
        wf = load(wf_path)
        for f in WORKFLOW_REQUIRED:
            if f not in wf or wf.get(f) in (None, "", [], {}):
                blockers.append(f"workflow card: required field missing/empty: {f}")
            elif has_todo(wf.get(f)):
                todo_count += 1
                blockers.append(f"workflow card: {f} still TODO")
        for f, p in (wf.get("provenance") or {}).items():
            if isinstance(p, dict) and p.get("source") == "assumed":
                assumed_fields.append(f)
        owners = wf.get("owners") or {}
        named = {v for v in owners.values() if isinstance(v, str) and not v.startswith("TODO")}
        if len(named) == 1 and len(owners) >= 3:
            warnings.append("owner concentration: one human holds all owner roles — name a second reviewer before R3")

        node_ids = [n["node_id"] if isinstance(n, dict) else n for n in wf.get("nodes", [])]
        edges = wf.get("edges") or []
        if not edges:
            blockers.append("workflow card: edges missing — the graph IS the process")
        else:
            referenced = {e.get("from") for e in edges} | {e.get("to") for e in edges}
            unknown = referenced - set(node_ids) - SINK_IDS
            if unknown:
                blockers.append(f"edges reference unknown nodes: {sorted(unknown)}")
            isolated = [n for n in node_ids if n not in referenced]
            if isolated:
                blockers.append(f"nodes not on the graph: {isolated}")
            adj: dict[str, list[str]] = {}
            for e in edges:
                adj.setdefault(e.get("from"), []).append(e.get("to"))
            if node_ids:
                seen, stack = set(), [node_ids[0]]
                while stack:
                    cur = stack.pop()
                    if cur in seen:
                        continue
                    seen.add(cur)
                    stack.extend(adj.get(cur, []))
                unreachable = [n for n in node_ids if n not in seen]
                if unreachable:
                    blockers.append(f"nodes unreachable from entry {node_ids[0]}: {unreachable}")
                for sink in SINK_IDS:
                    if sink == "happy_sink" and sink not in seen and not (set(adj.get(node_ids[-1], [])) & SINK_IDS):
                        warnings.append("no explicit edge into happy_sink — confirm the finished state lands somewhere")

        # node cards
        cw_proc = (crosswalk.get("process_nodes") or {})
        for nid in node_ids:
            card_path = pkg / "process" / "nodes" / f"{nid}.aac.json"
            if not card_path.exists():
                blockers.append(f"node card missing: process/nodes/{nid}.aac.json")
                continue
            nc = load(card_path)
            for f in NODE_REQUIRED:
                if f not in nc or nc.get(f) in (None, "", [], {}):
                    blockers.append(f"{nid}: required field missing/empty: {f}")
                elif has_todo(nc.get(f)):
                    todo_count += 1
                    blockers.append(f"{nid}: {f} still TODO")
            rm = nc.get("runtime_mode")
            if rm not in {"D", "C", "A", "H"}:
                if "TODO" in str(rm):
                    blockers.append(f"{nid}: runtime_mode still TODO — assign D/C/A/H (see runtime_suggestion on the card)")
                else:
                    blockers.append(f"{nid}: runtime_mode must be D/C/A/H")
            if nc.get("runtime_mode") in {"C", "A"}:
                ai_nodes.append(nid)
                for f in C_NODE_REQUIRED:
                    if f not in nc:
                        blockers.append(f"{nid}: C/A node missing {f}")
                    elif has_todo(nc.get(f)):
                        todo_count += 1
                        blockers.append(f"{nid}: {f} still TODO")
                pref = nc.get("prompt_ref")
                if isinstance(pref, str) and not (pkg / pref).exists():
                    blockers.append(f"{nid}: prompt_ref file missing: {pref}")
            cref = nc.get("concept_ref")
            if not cref or str(cref).startswith("TODO"):
                blockers.append(f"{nid}: concept_ref unmapped (TODO)")
            elif concept_ids and cref not in concept_ids:
                blockers.append(f"{nid}: concept_ref {cref} not found in concept map")
            if nid not in cw_proc:
                warnings.append(f"{nid}: not in crosswalk.process_nodes — re-run concept_to_process.py")

        # trunk coverage (a process node serving a member concept covers that member's trunk parent)
        covered = {load(pkg / "process" / "nodes" / f"{nid}.aac.json").get("concept_ref")
                   for nid in node_ids if (pkg / "process" / "nodes" / f"{nid}.aac.json").exists()}
        parent_of: dict[str, str] = {}
        for e in rivermap.get("edges", []):
            if not e.get("is_trunk"):
                parent_of.setdefault(e["to"], e["from"])
        covered_expanded = set(covered)
        for c in list(covered):
            seen_up: set[str] = set()
            p = parent_of.get(c)
            while p and p not in seen_up:
                seen_up.add(p)
                covered_expanded.add(p)
                p = parent_of.get(p)
        uncovered_trunk = [c for c in trunk_concept_ids if c in concept_ids and c not in covered_expanded]
        if uncovered_trunk:
            labels = {n["id"]: n["label"] for n in rivermap.get("nodes", [])}
            warnings.append("trunk concepts with no process node: "
                            + ", ".join(labels.get(c, c) for c in uncovered_trunk))

        # golden set — gates JUDGMENT, not plumbing. Required only when C/A nodes exist:
        # each agent harvests ITS OWN 3-5 real records from ITS OWN system of record (S5).
        gref = wf.get("golden_set_ref", f"process/evals/{pkg.name}.golden.json")
        gpath = pkg / gref if not Path(gref).is_absolute() else Path(gref)
        if not ai_nodes:
            warnings.append("no C/A nodes — golden set not required (deterministic/human-only workflow)")
            if gpath.exists():
                examples = load(gpath)
                examples = examples if isinstance(examples, list) else examples.get("examples", [])
                graded = sum(1 for e in examples if e.get("grade") in {"right", "wrong", "edit"})
                pending = sum(1 for e in examples if e.get("grade") in {None, "", "pending"})
        elif not gpath.exists():
            blockers.append(f"golden set missing: {gref} — harvest from this workflow's own system of record (pending)")
        else:
            examples = load(gpath)
            examples = examples if isinstance(examples, list) else examples.get("examples", [])
            if len(examples) < 3:
                blockers.append(f"golden set has {len(examples)} examples — harvest at least 3 real records "
                                f"from this workflow's own system of record (pending)")
            graded = sum(1 for e in examples if e.get("grade") in {"right", "wrong", "edit"})
            pending = sum(1 for e in examples if e.get("grade") in {None, "", "pending"})
            if pending:
                human_gates.append(f"golden set: {pending} example(s) pending human grade (grade, never author)")

    # ---------- Ladder ----------
    r0_structural = [b for b in blockers if "TODO" not in b and "pending" not in b]
    r0 = not r0_structural and atlas_ok and lint_pass and wf_path.exists()
    r1 = r0 and todo_count == 0 and not any("concept_ref" in b for b in blockers)
    r2 = r1 and (not ai_nodes or (pending == 0 and graded >= 3))
    ladder = {
        "R0_design_scaffold": {"pass": r0, "blocked_by": r0_structural},
        "R1_read_recommend": {"pass": r1,
                              "blocked_by": ([] if r1 else [f"{todo_count} TODO field(s)"] +
                                             ([f"{len(assumed_fields)} assumed-provenance field(s): {assumed_fields}"] if assumed_fields else [])),
                              "human_gate": "AgentTwin grade B or better"},
        "R2_draft": {"pass": r2,
                     "blocked_by": [] if r2 else [f"golden set graded {graded}, pending {pending}"],
                     "human_gate": ("named owner confirms golden threshold" if ai_nodes
                                    else "n/a — no judgment nodes; golden set not required")},
        "R3_supervised_send": {"pass": False, "human_gate": "clean run cards for the cadence window — human reviews"},
        "R4_autonomous": {"pass": False, "human_gate": "Go-Live Gate: human reviews lane_promotion evidence"},
    }

    report = {
        "package": pkg.name,
        "spine": atlas.get("spine", ""),
        "summary": {
            "blockers": len(blockers), "warnings": len(warnings), "human_gates": len(human_gates),
            "todo_fields": todo_count, "assumed_provenance_fields": assumed_fields,
            "golden_graded": graded, "golden_pending": pending,
        },
        "ladder": ladder,
        "blockers": blockers,
        "warnings": warnings,
        "human_gates": human_gates,
        "note": "This report maps readiness only. Lane promotion, sends, and writes remain human decisions.",
    }
    out = pkg / "exports" / "readiness_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n=== READINESS — {pkg.name} ===")
    for ring, info in ladder.items():
        mark = "PASS" if info.get("pass") else "BLOCKED"
        print(f"  {ring}: {mark}" + (f" | human gate: {info['human_gate']}" if info.get("human_gate") else ""))
    print(f"  blockers={len(blockers)} warnings={len(warnings)} todo={todo_count} "
          f"golden graded/pending={graded}/{pending}")
    if blockers:
        print("\nTop blockers:")
        for b in blockers[:12]:
            print(f"  - {b}")
        if len(blockers) > 12:
            print(f"  ... and {len(blockers) - 12} more (see exports/readiness_report.json)")
    print(f"\nWrote {out}")

    cs_path = pkg / "CONTROL_STATE.json"
    if cs_path.exists():
        cs = load(cs_path)
        ring = "R2" if r2 else ("R1" if r1 else "R0")
        cs.setdefault("readiness", {}).update({
            "ring": ring, "status": "PASS" if r0 else "BLOCKED",
            "report": "exports/readiness_report.json",
        })
        cs_path.write_text(json.dumps(cs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if strict and not r0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

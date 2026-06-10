#!/usr/bin/env python3
"""
Dark-factory-style QA for an AAC Agent Package.

Information barrier, adapted to design-time packages:
  - The auditor re-derives every expectation from the SOURCES (atlas.json, source packet,
    schema requirement lists) and grades the built artifacts blind. It never trusts
    CONTROL_STATE, generator logs, or builder claims; it re-runs lint itself.
  - Scenario criteria live in <package>/.holdout/scenarios/agent-package.yml (gitignored).
    Deterministic scenarios are graded here; judgment scenarios are marked llm_required
    and can be graded blind via the dark-factory-qa skill (run-qa.sh) without build context.
  - The bridge writes exports/qa_findings.md: WHAT failed only, never criteria or weights.

Scoring (StrongDM holdout style): PASS=1.0 PARTIAL=0.5 FAIL=0.0, weights critical=3 major=2
minor=1, satisfaction = sum(result*weight)/sum(weight). Verdict: block on any critical FAIL
or score < threshold; revise on any major FAIL; else allow.

Output: <package>/exports/qa_report.json, exports/qa_findings.md,
        <package>/.holdout/results/qa-<n>.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from validate_agent_package import NODE_REQUIRED, C_NODE_REQUIRED, WORKFLOW_REQUIRED, SINK_IDS

WEIGHTS = {"critical": 3, "major": 2, "minor": 1}
THRESHOLD = 0.75

LEAK_PATTERNS = [
    ("email_address", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}")),
    ("phone_number", re.compile(r"\b\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("ssn_pattern", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("api_key", re.compile(r"sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |EC )?PRIVATE KEY")),
]
SCAN_SUFFIXES = {".json", ".md", ".txt", ".sql", ".yml", ".mmd"}

# Judgment criteria the deterministic auditor cannot grade — run blind via dark-factory-qa.
LLM_SCENARIOS = [
    {"id": "concept_map_explains_why", "weight": "major",
     "question": "Does the concept map read as sense-making (why the agent exists) rather than a renamed step list?"},
    {"id": "spine_is_falsifiable", "weight": "major",
     "question": "Is the spine one falsifiable claim (who + what + win) that a measurement could break?"},
    {"id": "cards_in_owner_language", "weight": "minor",
     "question": "Could the named owner read each node card's deliverable and know what 'done' means without jargon?"},
]


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def grade(scenarios: list[dict]) -> tuple[float, str]:
    gradeable = [s for s in scenarios if not s.get("llm_required")]
    total = sum(WEIGHTS[s["weight"]] for s in gradeable) or 1
    score = sum(WEIGHTS[s["weight"]] * {"PASS": 1.0, "PARTIAL": 0.5, "FAIL": 0.0}[s["result"]]
                for s in gradeable) / total
    critical_fail = any(s["result"] == "FAIL" and s["weight"] == "critical" for s in gradeable)
    major_fail = any(s["result"] == "FAIL" and s["weight"] == "major" for s in gradeable)
    if critical_fail or score < THRESHOLD:
        return score, "block"
    return score, ("revise" if major_fail else "allow")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: python scripts/qa_agent_package.py <package_dir>")
    pkg = Path(args[0]).resolve()
    S: list[dict] = []

    def add(sid: str, weight: str, question: str, result: str, evidence: str,
            auto_fixable: bool = False, fix_class: str = "") -> None:
        S.append({"id": sid, "weight": weight, "question": question, "result": result,
                  "evidence": evidence, "auto_fixable": auto_fixable, "fix_class": fix_class})

    # ---- sources (the spec side of the barrier) ----
    atlas = load(pkg / "atlas" / "atlas.json", {})
    spine = str(atlas.get("spine", ""))
    spine_ok = bool(spine) and not spine.startswith("TODO") and 3 <= len(atlas.get("spine_path") or []) <= 9
    add("spine_locked", "critical", "Spine locked: one claim, 3-9 trunk nodes?",
        "PASS" if spine_ok else "FAIL", f"spine_path={len(atlas.get('spine_path') or [])}")

    untagged = [n.get("id") for n in atlas.get("nodes", [])
                if not all(n.get(k) for k in ("confidence", "provenance", "status"))]
    add("atlas_nodes_tagged", "major", "Every ATLAS node carries confidence/provenance/status?",
        "PASS" if not untagged else "FAIL", f"untagged={untagged[:6]}")

    # ---- concept layer: re-run lint blind ----
    map_dirs = sorted(pkg.glob("*_concept_map"))
    if map_dirs:
        r = subprocess.run([sys.executable, "scripts/lint_db.py"], cwd=map_dirs[0],
                           capture_output=True, text=True)
        add("lint_clean", "critical", "Concept map lint passes when re-run by the auditor?",
            "PASS" if r.returncode == 0 else "FAIL", (r.stdout or r.stderr).strip()[:200])
        export = map_dirs[0] / "knowledge" / "exports" / "latest.rivermap.json"
        db = map_dirs[0] / "knowledge" / "knowledge.db"
        fresh = export.exists() and (not db.exists() or db.stat().st_mtime <= export.stat().st_mtime + 1)
        add("exports_fresh", "minor", "Concept exports exist and are newer than the DB?",
            "PASS" if fresh else "FAIL", str(export.relative_to(pkg)) if export.exists() else "missing",
            auto_fixable=True, fix_class="stale_exports")
        rivermap = load(export, {})
    else:
        add("lint_clean", "critical", "Concept map exists?", "FAIL", "no *_concept_map dir")
        rivermap = {}
    concept_ids = {n["id"] for n in rivermap.get("nodes", [])}

    # ---- crosswalk: re-derive expectation from atlas ----
    cw = load(pkg / "atlas" / "crosswalk.json", {})
    atlas_ids = {n.get("id") for n in atlas.get("nodes", [])}
    mapped = set((cw.get("atlas_nodes") or {}).keys())
    add("crosswalk_complete", "major", "Every ATLAS node maps into the concept map?",
        "PASS" if atlas_ids and atlas_ids <= mapped else ("PARTIAL" if mapped else "FAIL"),
        f"unmapped={sorted(atlas_ids - mapped)[:6]}")

    # ---- process layer ----
    wf = load(pkg / "process" / "workflow.aac.json", {})
    node_ids = [n["node_id"] if isinstance(n, dict) else n for n in wf.get("nodes", [])]
    missing_wf = [f for f in WORKFLOW_REQUIRED if not wf.get(f)]
    add("workflow_required_fields", "critical", "Workflow card carries every required AAC field?",
        "PASS" if wf and not missing_wf else "FAIL", f"missing={missing_wf[:8]}")

    edges = wf.get("edges") or []
    targets = {e.get("to") for e in edges}
    sinks_wired = bool(edges) and ("refuse_sink" in targets or "hard_refuse_sink" in targets)
    add("graph_has_refusal_paths", "critical", "Graph wires refusal/hard-refuse sinks (refuse is first-class)?",
        "PASS" if sinks_wired else "FAIL", f"sink_targets={sorted(targets & SINK_IDS)}")

    cards = {nid: load(pkg / "process" / "nodes" / f"{nid}.aac.json", {}) for nid in node_ids}
    no_card = [nid for nid, c in cards.items() if not c]
    add("cards_exist_per_node", "critical", "One AAC card per node?",
        "PASS" if node_ids and not no_card else "FAIL", f"missing={no_card}")

    incomplete, telemetry_bad, sup_bad, h_unjustified, obj_bad, exec_bad = [], [], [], [], [], []
    ai_nodes = []
    for nid, c in cards.items():
        if not c:
            continue
        if [f for f in NODE_REQUIRED if f not in c or c.get(f) in (None, "", [], {})]:
            incomplete.append(nid)
        t = c.get("telemetry") or {}
        if not (t.get("run_card_required") and t.get("metrics") and t.get("artifact_path")):
            telemetry_bad.append(nid)
        rm = c.get("runtime_mode")
        if rm in {"C", "A"}:
            ai_nodes.append(nid)
            sup = c.get("supervision") or {}
            if sup.get("mode") != "human_over_loop" or sup.get("inline_approval") is not False:
                sup_bad.append(nid)
            obj = c.get("objective") or {}
            if not ((obj.get("primary") or {}).get("metric") and obj.get("improvement_policy")):
                obj_bad.append(nid)
        if not ((c.get("execution") or {}).get("harness")):
            exec_bad.append(nid)
        if rm == "H":
            j = str((c.get("supervision") or {}).get("justification", ""))
            if not j or j.startswith("TODO"):
                h_unjustified.append(nid)
    add("card_items_complete", "major", "Every card carries all AAC items (contracts, disciplines, gates, owner, deliverable, artifact, kill switch)?",
        "PASS" if not incomplete else ("PARTIAL" if len(incomplete) < len(cards) else "FAIL"),
        f"incomplete={incomplete[:6]}")
    add("telemetry_per_node", "critical", "Every node declares run-card telemetry (metrics + artifact path)?",
        "PASS" if cards and not telemetry_bad else "FAIL", f"bad={telemetry_bad[:6]}",
        auto_fixable=bool(telemetry_bad), fix_class="derivable_fields")
    add("supervision_over_loop", "major", "Every C/A node runs human-OVER-the-loop (async queue, no inline approval)?",
        "PASS" if not sup_bad else "FAIL", f"bad={sup_bad[:6]}",
        auto_fixable=bool(sup_bad), fix_class="derivable_fields")
    add("h_nodes_justified", "major", "Every inline human gate carries a highest-risk justification?",
        "PASS" if not h_unjustified else "FAIL", f"unjustified={h_unjustified[:6]}")
    ap_ok = bool(wf.get("automation_policy")) and bool(wf.get("runtime_target"))
    add("automation_policy_present", "major",
        "Workflow card declares the automation policy (named human gates) and a runtime_target?",
        "PASS" if ap_ok else "FAIL", "automation_policy + runtime_target",
        auto_fixable=not ap_ok, fix_class="derivable_fields")
    add("execution_declared", "minor",
        "Every node declares its execution harness (what runs it and where)?",
        "PASS" if cards and not exec_bad else "FAIL", f"missing={exec_bad[:6]}",
        auto_fixable=bool(exec_bad), fix_class="derivable_fields")
    if ai_nodes:
        add("objective_present", "major",
            "Every judgment node carries a measurable objective function (metric + improvement policy)?",
            "PASS" if not obj_bad else "FAIL", f"missing={obj_bad[:6]}",
            auto_fixable=bool(obj_bad), fix_class="derivable_fields")

    # golden policy: judgment nodes only
    if ai_nodes:
        gref = wf.get("golden_set_ref", f"process/evals/{pkg.name}.golden.json")
        g = load(pkg / gref, [])
        g = g if isinstance(g, list) else g.get("examples", [])
        graded = sum(1 for e in g if e.get("grade") in {"right", "wrong", "edit"})
        pending = sum(1 for e in g if e.get("grade") in {None, "", "pending"})
        result = "PASS" if graded >= 3 and pending == 0 else ("PARTIAL" if len(g) >= 3 else "FAIL")
        add("golden_set_policy", "major",
            "Judgment nodes have a harvested golden set (graded = human-over-loop queue item, not inline)?",
            result, f"examples={len(g)} graded={graded} pending={pending}")
    prompts_missing = [nid for nid in ai_nodes
                       if not (pkg / (cards[nid].get("prompt_ref") or f"process/prompts/{nid}.md")).exists()]
    if ai_nodes:
        add("prompts_exist", "minor", "Every C/A node has its prompt file?",
            "PASS" if not prompts_missing else "FAIL", f"missing={prompts_missing}",
            auto_fixable=bool(prompts_missing), fix_class="missing_stubs")

    # ---- leak scan (hard refuse class) ----
    leaks = []
    for f in pkg.rglob("*"):
        if f.suffix not in SCAN_SUFFIXES or not f.is_file() or ".holdout" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for name, pat in LEAK_PATTERNS:
            m = pat.search(text)
            if m:
                leaks.append(f"{f.relative_to(pkg)}: {name} ({m.group(0)[:4]}…redacted)")
    add("no_leaked_identifiers", "critical", "No emails/phones/SSN/API-key patterns anywhere in the package?",
        "PASS" if not leaks else "FAIL", "; ".join(leaks[:5]) or "clean")

    for s in LLM_SCENARIOS:
        S.append({**s, "result": "PENDING_LLM", "evidence": "grade blind via dark-factory-qa run-qa.sh",
                  "llm_required": True, "auto_fixable": False, "fix_class": ""})

    score, verdict = grade(S)
    report = {
        "package": pkg.name, "verdict": verdict, "satisfaction": round(score, 3),
        "threshold": THRESHOLD,
        "summary": {"pass": sum(1 for s in S if s["result"] == "PASS"),
                    "partial": sum(1 for s in S if s["result"] == "PARTIAL"),
                    "fail": sum(1 for s in S if s["result"] == "FAIL"),
                    "pending_llm": sum(1 for s in S if s["result"] == "PENDING_LLM"),
                    "auto_fixable": sum(1 for s in S if s["result"] != "PASS" and s.get("auto_fixable"))},
        "scenarios": S,
        "barrier_note": "Auditor re-derived expectations from atlas + schemas only; lint re-run independently.",
    }
    out = pkg / "exports" / "qa_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # holdout copies: scenario criteria + full result (builder reads findings only)
    holdout = pkg / ".holdout"
    (holdout / "scenarios").mkdir(parents=True, exist_ok=True)
    (holdout / "results").mkdir(parents=True, exist_ok=True)
    scen_yaml = ["name: agent-package QA", "version: 1", "domain: config",
                 f"satisfaction_threshold: {THRESHOLD}", "scenarios:"]
    for s in S:
        scen_yaml += [f"  - id: {s['id']}", f"    question: \"{s['question']}\"",
                      f"    weight: {s['weight']}"]
    (holdout / "scenarios" / "agent-package.yml").write_text("\n".join(scen_yaml) + "\n", encoding="utf-8")
    n = len(list((holdout / "results").glob("qa-*.json"))) + 1
    (holdout / "results" / f"qa-{n:03d}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # bridge: findings only — what failed, never weights/criteria
    findings = [f"# QA findings — {pkg.name}", f"Verdict: {verdict.upper()}", ""]
    for s in S:
        if s["result"] in {"FAIL", "PARTIAL"}:
            findings.append(f"- {s['result']}: {s['evidence'] or s['id']}")
    if report["summary"]["pending_llm"]:
        findings.append(f"- {report['summary']['pending_llm']} judgment check(s) await the blind LLM pass")
    (pkg / "exports" / "qa_findings.md").write_text("\n".join(findings) + "\n", encoding="utf-8")

    print(f"QA verdict: {verdict.upper()} (satisfaction {score:.2f} / {THRESHOLD}, "
          f"fail={report['summary']['fail']}, auto-fixable={report['summary']['auto_fixable']})")
    for s in S:
        if s["result"] == "FAIL":
            print(f"  FAIL [{s['weight']}] {s['id']}: {s['evidence'][:90]}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

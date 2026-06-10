#!/usr/bin/env python3
"""
S6 Compiler: certified cards in -> runnable agent out. Deterministic: same cards, same build.

Reads  <package>/process/workflow.aac.json + nodes/*.aac.json + prompts/*.md + factory.config.json
Emits  <package>/build/
         agent/main.py          orchestrator runtime (gates, routing, run cards, kill switch)
         agent/llm_adapters.py  harness adapters (anthropic-api / claude-code-headless /
                                openai-compatible: openai, deepseek, mistral, gemini / ollama)
         agent/runcard.py       run-card contract (telemetry is physical)
         agent/nodes.json       compiled graph
         agent/prompts/<id>.md  assembled system prompts per C/A node
         agent/handlers/<id>.py D-node business-logic stubs (NEVER overwritten)
         deploy/                trigger snippets per runtime_target (systemd timer / cron)
         BUILD_REPORT.md        what compiled, in which lane, and every warning

Compile gates (refusals, not warnings):
  - validator R0 must pass (structure)
  - every C/A node needs model + prompt file; every node needs an execution harness
Lane honesty: if R1 is blocked (TODO fields) or goldens are ungraded, the build is forced to
SHADOW lane (internal_artifact_only) regardless of the card's max_lane. Promotion never compiles
external effectors — this runtime contains none by construction.

Usage: python3 scripts/compile_agent.py <package_dir> [--force-shadow]
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
TEMPLATES = SCRIPTS / "runtime_templates"


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def slug_module(node_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", node_id.lower())


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: python3 scripts/compile_agent.py <package_dir> [--force-shadow]")
    pkg = Path(args[0]).resolve()
    wf = load(pkg / "process" / "workflow.aac.json")
    if not wf:
        raise SystemExit("REFUSED: no workflow card — run the pipeline first.")

    # Gate 1: validator (structure). Honest readiness feeds the lane decision.
    subprocess.run([sys.executable, str(SCRIPTS / "validate_agent_package.py"), str(pkg)],
                   capture_output=True, text=True)
    readiness = load(pkg / "exports" / "readiness_report.json", {})
    ladder = readiness.get("ladder", {})
    if not (ladder.get("R0_design_scaffold") or {}).get("pass"):
        raise SystemExit("REFUSED: R0 structure fails — fix blockers in exports/readiness_report.json first.")
    r1_pass = bool((ladder.get("R1_read_recommend") or {}).get("pass"))
    r2_pass = bool((ladder.get("R2_draft") or {}).get("pass"))

    warnings: list[str] = []
    shadow = "--force-shadow" in sys.argv or not (r1_pass and r2_pass)
    if shadow:
        why = []
        if not r1_pass:
            why.append("R1 blocked (TODO fields)")
        if not r2_pass:
            why.append("R2 blocked (golden grades)")
        warnings.append("SHADOW LANE forced: " + (", ".join(why) or "--force-shadow") +
                        " — outputs are internal artifacts only")
    lane = "internal_artifact_only" if shadow else str(wf.get("max_lane", "recommend"))

    node_ids = [n["node_id"] if isinstance(n, dict) else n for n in wf.get("nodes", [])]
    cards = {nid: load(pkg / "process" / "nodes" / f"{nid}.aac.json") for nid in node_ids}
    missing = [nid for nid, c in cards.items() if not c]
    if missing:
        raise SystemExit(f"REFUSED: node cards missing: {missing}")

    # Gate 2: C/A nodes need model + prompt + harness
    for nid, c in cards.items():
        if not (c.get("execution") or {}).get("harness"):
            raise SystemExit(f"REFUSED: {nid} has no execution.harness (run heal, then recompile)")
        if c.get("runtime_mode") in ("C", "A"):
            model = str(c.get("model", ""))
            if not model or model.startswith("TODO"):
                raise SystemExit(f"REFUSED: {nid} has no model. Set it on the card, in factory.config.json, "
                                 f"or run improve_node.py to adopt one through the holdout gate.")
            if not (pkg / c.get("prompt_ref", "")).exists():
                raise SystemExit(f"REFUSED: {nid} prompt file missing: {c.get('prompt_ref')}")

    build = pkg / "build"
    agent = build / "agent"
    for d in (agent / "prompts", agent / "handlers", build / "deploy"):
        d.mkdir(parents=True, exist_ok=True)

    # Runtime + adapters + run-card contract (self-contained copy)
    shutil.copy(TEMPLATES / "agent_runtime.py", agent / "main.py")
    shutil.copy(TEMPLATES / "llm_adapters.py", agent / "llm_adapters.py")
    shutil.copy(SCRIPTS / "runcard.py", agent / "runcard.py")
    (agent / "handlers" / "__init__.py").write_text("", encoding="utf-8")

    # Compiled graph
    edges_by_from: dict[str, list[dict]] = {}
    for e in wf.get("edges", []):
        edges_by_from.setdefault(e["from"], []).append({"to": e["to"], "condition": e.get("condition", "always")})

    graph_nodes = {}
    for nid in node_ids:
        c = cards[nid]
        module = slug_module(nid)
        rm = c.get("runtime_mode")
        floor = (c.get("gates", {}).get("output", {}) or {}).get("confidence_floor")
        node_edges = edges_by_from.get(nid, [])
        human_targets = [e["to"] for e in node_edges
                         if cards.get(e["to"], {}).get("runtime_mode") == "H"] or ["refuse_sink"]
        graph_nodes[nid] = {
            "node_id": nid, "module": module, "runtime_mode": rm,
            "execution": c.get("execution", {}), "model": c.get("model"),
            "prompt_version": c.get("prompt_version"),
            "confidence_floor": float(floor) if isinstance(floor, (int, float)) else 0.6,
            "bounded_actions": c.get("bounded_actions") or [],
            "hard_refuse": c.get("hard_refuse", []),
            "edges": node_edges,
            "below_floor_route": human_targets[0],
            "default_route": node_edges[0]["to"] if len(node_edges) == 1 else "refuse_sink",
        }
        if rm in ("C", "A"):
            prompt_src = (pkg / c["prompt_ref"]).read_text(encoding="utf-8")
            contract = json.dumps(c.get("output_contract", {}), indent=2)
            hard = "\n".join(f"- {h}" for h in c.get("hard_refuse", []))
            assembled = (
                f"{prompt_src}\n\n## Output contract (respond with ONLY this JSON)\n"
                f"{{\"decision\": \"<one bounded action>\", \"confidence\": <0..1>, "
                f"\"rationale\": \"<=280 chars\", \"citations\": [\"<source ids>\"]}}\n\n"
                f"Card output_contract reference:\n{contract}\n\n"
                f"## Hard refuse (output decision \"hard_refuse\" + reason if ANY apply)\n{hard}\n\n"
                f"## Refusal\nIf input is out-of-distribution, ambiguous, or below your confidence, "
                f"set decision \"refuse\" with the reason. Refusal is always acceptable.\n")
            (agent / "prompts" / f"{module}.md").write_text(assembled, encoding="utf-8")
        if rm == "D":
            stub = agent / "handlers" / f"{module}.py"
            if not stub.exists():
                purpose = c.get("purpose", "")
                contract_in = json.dumps(c.get("input_contract", {}), indent=2)
                contract_out = json.dumps(c.get("output_contract", {}), indent=2)
                stub.write_text(
                    f'"""D-node handler: {nid}\nPurpose: {purpose}\n\nInput contract:\n{contract_in}\n\n'
                    f'Output contract:\n{contract_out}\n\nImplement run(ctx) and remove the '
                    f'_unimplemented flag. The runtime treats the flag as a logged pass-through '
                    f'(shadow lane only).\n"""\n\n\ndef run(ctx: dict) -> dict:\n'
                    f'    return {{"_unimplemented": True, **ctx}}\n', encoding="utf-8")
            else:
                warnings.append(f"{nid}: existing handler kept (not overwritten)")

    entry = node_ids[0]
    (agent / "nodes.json").write_text(json.dumps({
        "workflow_id": wf.get("workflow_id"), "entry": entry, "lane": lane,
        "compiled_from": "process/workflow.aac.json", "nodes": graph_nodes,
    }, indent=2) + "\n", encoding="utf-8")

    # Deploy snippets per runtime_target
    target = str(wf.get("runtime_target", "")).split()[0].strip(":") or "TODO"
    wf_id = wf.get("workflow_id", "agent")
    (build / "deploy" / "cron.txt").write_text(
        f"# local-cron / vps-scheduler: daily sweep at 06:00\n"
        f"0 6 * * * cd {pkg} && python3 build/agent/main.py '{{}}' >> build/agent.log 2>&1\n",
        encoding="utf-8")
    (build / "deploy" / f"{wf_id}.service").write_text(
        f"[Unit]\nDescription=AAC agent {wf_id} (one run)\n\n[Service]\nType=oneshot\n"
        f"WorkingDirectory={pkg}\nExecStart=/usr/bin/python3 {pkg}/build/agent/main.py {{}}\n",
        encoding="utf-8")
    (build / "deploy" / f"{wf_id}.timer").write_text(
        f"[Unit]\nDescription=Daily sweep for {wf_id}\n\n[Timer]\nOnCalendar=*-*-* 06:00:00\n"
        f"Persistent=true\n\n[Install]\nWantedBy=timers.target\n", encoding="utf-8")

    d_stubs = [nid for nid, g in graph_nodes.items()
               if g["runtime_mode"] == "D" and "_unimplemented" in
               (agent / "handlers" / f"{g['module']}.py").read_text(encoding="utf-8")]
    report = [
        f"# Build report — {wf_id}",
        f"Lane: **{lane}**" + (" (shadow-forced)" if shadow else ""),
        f"Runtime target: {target} (snippets in build/deploy/)",
        f"Nodes: {len(node_ids)} | entry: {entry}",
        f"C/A models: " + ", ".join(f"{nid}={cards[nid].get('model')}" for nid in node_ids
                                     if cards[nid].get("runtime_mode") in ("C", "A")),
        f"D handlers to implement: {d_stubs or 'none'}",
        "",
        "Run once now:  python3 build/agent/main.py '{}'",
        "Smoke (no API): FACTORY_FAKE_LLM=1 python3 build/agent/main.py '{}'",
        "Kill switch:   touch build/KILL   (every run aborts until removed)",
        "",
        "## Warnings",
        *(f"- {w}" for w in warnings or ["- none"]),
        "",
        "External effectors compiled into this runtime: **none** (by construction).",
    ]
    (build / "BUILD_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print(f"COMPILED {wf_id} -> {build.relative_to(pkg)} | lane={lane} | nodes={len(node_ids)} | "
          f"warnings={len(warnings)}")
    for w in warnings[:6]:
        print(f"  ! {w}")
    print(f"  run: FACTORY_FAKE_LLM=1 python3 {build / 'agent' / 'main.py'} '{{}}'")


if __name__ == "__main__":
    main()

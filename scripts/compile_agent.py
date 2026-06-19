#!/usr/bin/env python3
"""
S6 Compiler: certified cards in -> runnable agent out. Deterministic: same cards, same build.

v0.3 adds runtime-truth compile gates: executable route conditions and certifying
output gates must parse before the build exists; Hermes cron gets a real wrapper;
and run cards carry requested/actual model and honest unknown telemetry.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
TEMPLATES = SCRIPTS / "runtime_templates"
sys.path.insert(0, str(SCRIPTS))

from factory_config import load_config  # noqa: E402
from gate_grammar import (  # noqa: E402
    is_advisory_text,
    normalize_gate_declaration,
    parse_condition,
    parse_gate_rule,
)


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def slug_module(node_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", node_id.lower())


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _validate_executable_condition(label: str, condition: str) -> None:
    try:
        parse_condition(condition)
    except Exception as e:
        raise SystemExit(f"REFUSED: {label} has non-machine-evaluable condition {condition!r}: {e}")


def collect_output_gates(card: dict, nid: str, warnings: list[str]) -> tuple[list[dict], list[str], list[str]]:
    """Return (compiled_gates, blocking_unenforced, advisory_notes)."""
    compiled: list[dict] = []
    blocking: list[str] = []
    advisory: list[str] = []

    def add(raw: Any, source: str, certifying_default: bool = True) -> None:
        gate = normalize_gate_declaration(raw)
        if "certifying" not in gate:
            gate["certifying"] = certifying_default
        rule = str(gate.get("rule") or "").strip()
        gate_type = str(gate.get("gate_type", "deterministic"))
        if gate_type == "design_time_only" and gate.get("certifying"):
            raise SystemExit(f"REFUSED: {nid} {source} is certifying but design_time_only: {rule}")
        if gate_type == "llm_semantic":
            # v0.3 can carry LLM semantic gates, but certification needs external calibration evidence.
            if gate.get("certifying") and gate.get("evidence_required") != "holdout_calibrated":
                blocking.append(f"{nid}:{gate.get('gate_id')} llm_semantic gate lacks holdout_calibrated evidence")
            compiled.append(gate)
            return
        try:
            parse_gate_rule(rule)
        except Exception as e:
            if gate.get("certifying", True):
                raise SystemExit(f"REFUSED: {nid} {source} is not executable: {rule!r}: {e}")
            advisory.append(f"{nid}:{source}: {rule}")
            return
        compiled.append(gate)

    output = (card.get("gates") or {}).get("output", {}) or {}
    for raw in _as_list(card.get("output_gates")):
        add(raw, "output_gates")
    for raw in _as_list(card.get("certifying_gates")):
        add(raw, "certifying_gates")
    for key in ("rules", "certifying_rules", "certifying_checks"):
        for raw in _as_list(output.get(key)):
            add(raw, f"gates.output.{key}")
    for check in _as_list(output.get("checks")):
        if not isinstance(check, str):
            add(check, "gates.output.checks")
            continue
        if is_advisory_text(check):
            advisory.append(f"{nid}:gates.output.checks advisory: {check[:100]}")
            continue
        # A non-advisory output check is behavior-critical. It must be executable.
        add(check, "gates.output.checks")

    for field in ("runtime_gate", "certification_gate"):
        for raw in _as_list(card.get(field)):
            add(raw, field)

    if blocking:
        warnings.extend(f"blocking unenforced gate: {b}" for b in blocking)
    return compiled, blocking, advisory


def enrich_execution(execution: dict, model: str, cfg: dict) -> dict:
    out = dict(execution or {})
    local = (cfg.get("model_preferences") or {}).get("local") or {}
    harness = out.get("harness")
    provider = local.get("provider")
    if harness in {"local-model", "ollama", "ollama-native"} or model.startswith("ollama/"):
        out.setdefault("endpoint", local.get("endpoint") or "http://localhost:11434")
    if harness in {"local-openai-compatible", "openai-compatible-local"} or provider in {"local-openai-compatible", "openai-compatible-local"}:
        out["harness"] = "local-openai-compatible"
        out.setdefault("base_url", local.get("endpoint") or "http://localhost:8000/v1")
    return out


def write_hermes_cron_deploy(build: Path, pkg: Path, wf_id: str) -> None:
    deploy = build / "deploy"
    wrapper = deploy / "hermes_no_agent_wrapper.py"
    wrapper.write_text(f'''#!/usr/bin/env python3
"""Hermes no_agent wrapper for {wf_id}. Generated by AAC Factory v0.3."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import main  # noqa: E402


def run(dry_run: bool = False) -> dict:
    if dry_run:
        return {{"status": "dry_run_ok", "workflow": {wf_id!r}, "wrapper": str(Path(__file__).resolve())}}
    loops = 0
    summaries = []
    # Narrow v0.3 batch pattern: one runtime call may process a finite candidates list.
    # If a gather handler later returns no_candidates, stop cleanly.
    while loops < 100:
        summary = main.run_once({{}})
        summaries.append(summary)
        loops += 1
        if summary.get("status") in {{"no_candidates", "killed"}}:
            break
        break
    return {{"status": "ok", "workflow": {wf_id!r}, "loops": loops, "summaries": summaries}}

if __name__ == "__main__":
    print(json.dumps(run("--dry-run" in sys.argv), indent=2))
''', encoding="utf-8")
    wrapper.chmod(0o755)
    (deploy / "hermes_cron.json").write_text(json.dumps({
        "name": f"aac-shadow-{wf_id}",
        "schedule": "0 6 * * *",
        "script": str(wrapper),
        "no_agent": True,
        "deliver": "local",
        "dry_run_validation": f"python3 {wrapper} --dry-run",
    }, indent=2) + "\n", encoding="utf-8")
    (deploy / "hermes_cron_create.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"python3 {wrapper} --dry-run >/dev/null\n"
        f"hermes cron create --name 'aac-shadow-{wf_id}' --schedule '0 6 * * *' --script '{wrapper}' --no-agent --deliver local\n",
        encoding="utf-8")
    (deploy / "hermes_cron_create.sh").chmod(0o755)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: python3 scripts/compile_agent.py <package_dir> [--force-shadow]")
    pkg = Path(args[0]).resolve()
    cfg = load_config()
    wf = load(pkg / "process" / "workflow.aac.json")
    if not wf:
        raise SystemExit("REFUSED: no workflow card — run the pipeline first.")

    subprocess.run([sys.executable, str(SCRIPTS / "validate_agent_package.py"), str(pkg)],
                   capture_output=True, text=True)
    readiness = load(pkg / "exports" / "readiness_report.json", {})
    ladder = readiness.get("ladder", {})
    if not (ladder.get("R0_design_scaffold") or {}).get("pass"):
        raise SystemExit("REFUSED: R0 structure fails — fix blockers in exports/readiness_report.json first.")
    r1_pass = bool((ladder.get("R1_read_recommend") or {}).get("pass"))
    r2_pass = bool((ladder.get("R2_draft") or {}).get("pass"))

    warnings: list[str] = []
    certifying_gates: list[str] = []
    blocking_unenforced_gates: list[str] = []
    advisory_design_notes: list[str] = []
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
    cards = {nid: load(pkg / "process" / "nodes" / f"{nid}.aac.json") or {} for nid in node_ids}
    missing = [nid for nid, c in cards.items() if not c]
    if missing:
        raise SystemExit(f"REFUSED: node cards missing: {missing}")

    for nid, c in cards.items():
        if not (c.get("execution") or {}).get("harness"):
            raise SystemExit(f"REFUSED: {nid} has no execution.harness (run heal, then recompile)")
        if c.get("runtime_mode") in ("C", "A"):
            model = str(c.get("model", ""))
            if not model or model.startswith("TODO"):
                raise SystemExit(f"REFUSED: {nid} has no model. Set it on the card, in factory.config.json, "
                                 f"or run improve_node.py to propose one through the holdout gate.")
            if not (pkg / c.get("prompt_ref", "")).exists():
                raise SystemExit(f"REFUSED: {nid} prompt file missing: {c.get('prompt_ref')}")

    edges_by_from: dict[str, list[dict]] = {}
    for i, e in enumerate(wf.get("edges", [])):
        cond = e.get("condition", "always")
        _validate_executable_condition(f"edge[{i}] {e.get('from')}->{e.get('to')}", cond)
        edges_by_from.setdefault(e["from"], []).append({"to": e["to"], "condition": cond})

    build = pkg / "build"
    agent = build / "agent"
    for d in (agent / "prompts", agent / "handlers", build / "deploy"):
        d.mkdir(parents=True, exist_ok=True)

    shutil.copy(TEMPLATES / "agent_runtime.py", agent / "main.py")
    shutil.copy(TEMPLATES / "llm_adapters.py", agent / "llm_adapters.py")
    shutil.copy(SCRIPTS / "runcard.py", agent / "runcard.py")
    shutil.copy(SCRIPTS / "gate_grammar.py", agent / "gate_grammar.py")
    (agent / "handlers" / "__init__.py").write_text("", encoding="utf-8")

    graph_nodes = {}
    for nid in node_ids:
        c = cards[nid]
        module = slug_module(nid)
        rm = c.get("runtime_mode")
        floor = (c.get("gates", {}).get("output", {}) or {}).get("confidence_floor")
        node_edges = edges_by_from.get(nid, [])
        human_targets = [e["to"] for e in node_edges
                         if cards.get(e["to"], {}).get("runtime_mode") == "H"] or ["refuse_sink"]
        output_gates, blocking, advisory = collect_output_gates(c, nid, warnings)
        certifying_gates.extend(f"{nid}:{g.get('gate_id')}" for g in output_gates if g.get("certifying"))
        blocking_unenforced_gates.extend(blocking)
        advisory_design_notes.extend(advisory)
        if blocking:
            warnings.append(f"{nid}: {len(blocking)} gate(s) are non-certifying until evidence exists")
        graph_nodes[nid] = {
            "node_id": nid, "module": module, "runtime_mode": rm,
            "execution": enrich_execution(c.get("execution", {}), str(c.get("model") or ""), cfg),
            "model": c.get("model"),
            "prompt_ref": c.get("prompt_ref"),
            "prompt_version": c.get("prompt_version"),
            "confidence_floor": float(floor) if isinstance(floor, (int, float)) else 0.6,
            "bounded_actions": c.get("bounded_actions") or [],
            "hard_refuse": c.get("hard_refuse", []),
            "tbr_gate": c.get("tbr_gate", {}),
            "output_gates": output_gates,
            "edges": node_edges,
            "below_floor_route": human_targets[0],
            "default_route": node_edges[0]["to"] if len(node_edges) == 1 else "refuse_sink",
        }
        if rm in ("C", "A"):
            prompt_src = (pkg / c["prompt_ref"]).read_text(encoding="utf-8")
            contract = json.dumps(c.get("output_contract", {}), indent=2)
            hard = "\n".join(f"- {h}" for h in c.get("hard_refuse", []))
            gates_txt = "\n".join(f"- {g['rule']}" for g in output_gates) or "- confidence/bounded/leak gates only"
            assembled = (
                f"{prompt_src}\n\n## Output contract (respond with ONLY this JSON)\n"
                f"{{\"decision\": \"<one bounded action>\", \"confidence\": <0..1>, "
                f"\"rationale\": \"<=280 chars\", \"citations\": [\"<source ids>\"]}}\n\n"
                f"Card output_contract reference:\n{contract}\n\n"
                f"## Executable output gates\n{gates_txt}\n\n"
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
    if target == "hermes-cron":
        write_hermes_cron_deploy(build, pkg, wf_id)

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
        "## Runtime truth gates",
        f"- certifying_gates: {certifying_gates or 'none'}",
        f"- blocking_unenforced_gates: {blocking_unenforced_gates or 'none'}",
        f"- advisory_design_notes: {len(advisory_design_notes)}",
        "- tbr_gate_compiled: true (Translator/Bouncer/Recorder proof fields carried into nodes.json + run cards)",
        "- model_verification_required: true for C/A certification",
        "- usage_required_for_cost_claims: true",
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

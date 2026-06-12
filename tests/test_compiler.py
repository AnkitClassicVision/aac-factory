#!/usr/bin/env python3
"""
S6 compiler regression: compile the synthetic package and RUN the compiled agent offline.

Proves: compile gates refuse dishonest builds; shadow lane forced on ungraded/TODO packages;
the compiled runtime walks the graph, enforces confidence floors and hard-refuse, writes a
run card per node execution, queues H-nodes for the human, and contains zero external actions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(REPO / "tests"))

from test_improvement_loop import build_pkg  # noqa: E402


def make_workflow_conditions_parseable(pkg: Path) -> None:
    wf_path = pkg / "process" / "workflow.aac.json"
    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    replacements = {
        "low confidence": "confidence < 0.6",
        "approved": "decision == \"queued_for_human\"",
        "violation": "decision == \"hard_refuse\"",
    }
    for edge in wf.get("edges", []):
        edge["condition"] = replacements.get(edge.get("condition"), edge.get("condition", "always"))
    wf_path.write_text(json.dumps(wf, indent=2), encoding="utf-8")


def run(cmd, cwd, env=None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=e)


def test_compiler() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        work.mkdir()
        pkg = build_pkg(work)
        (work / "scripts" / "runtime_templates").mkdir(exist_ok=True)
        for f in ("agent_runtime.py", "llm_adapters.py"):
            (work / "scripts" / "runtime_templates" / f).write_text(
                (REPO / "scripts" / "runtime_templates" / f).read_text(encoding="utf-8"), encoding="utf-8")
        (work / "scripts" / "compile_agent.py").write_text(
            (REPO / "scripts" / "compile_agent.py").read_text(encoding="utf-8"), encoding="utf-8")

        # Gate: model TODO must refuse
        judge_path = pkg / "process" / "nodes" / "t-judge.aac.json"
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        judge["model"] = "TODO: pick"
        judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")
        r = run([PY, "scripts/compile_agent.py", str(pkg)], work)
        assert r.returncode != 0 and "no model" in r.stdout + r.stderr, "must refuse TODO model"

        # Set a concrete model, then prove prose routing conditions fail compile.
        judge["model"] = "claude-sonnet-4-6"
        judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")
        r = run([PY, "scripts/compile_agent.py", str(pkg)], work)
        assert r.returncode != 0 and "non-machine-evaluable condition" in r.stdout + r.stderr, \
            "prose edge conditions must fail compile"

        # Fix the routing grammar, then compile.
        make_workflow_conditions_parseable(pkg)
        r = run([PY, "scripts/compile_agent.py", str(pkg)], work)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "COMPILED" in r.stdout
        build = pkg / "build"
        assert (build / "agent" / "main.py").exists() and (build / "agent" / "nodes.json").exists()
        graph = json.loads((build / "agent" / "nodes.json").read_text(encoding="utf-8"))
        # synthetic package has graded goldens (R2) but TODO fields (R1 blocked) -> shadow forced
        assert graph["lane"] == "internal_artifact_only", "shadow lane must be forced on R1-blocked package"

        # Implement the D handler so the walk is real, not pass-through
        (build / "agent" / "handlers" / "t_intake.py").write_text(
            "def run(ctx):\n    return {**ctx, 'records': [1, 2], 'decision': 'ingested', 'confidence': 1.0}\n",
            encoding="utf-8")

        # Happy path: fake LLM, high confidence -> t-judge -> t-review queues for human
        r = run([PY, str(build / "agent" / "main.py"), "{}"], work,
                env={"FACTORY_FAKE_LLM": "1", "FACTORY_FAKE_CONFIDENCE": "0.95"})
        assert r.returncode == 0, r.stdout + r.stderr
        summary = json.loads(r.stdout)
        assert summary["external_actions_taken"] == 0
        assert summary["outcome"] in ("happy_sink", "refuse_sink", "hard_refuse_sink")
        assert any("t-review" in q.name for q in
                   (pkg / "process" / "run-cards" / "_review_queue").glob("*.json")), \
            "H node must queue for the human"
        runs = list((pkg / "process" / "run-cards" / "t-judge").glob("*.json"))
        assert runs, "run card per node execution"

        # Low confidence -> below floor -> routes toward human/refuse, escalation recorded
        r = run([PY, str(build / "agent" / "main.py"), "{}"], work,
                env={"FACTORY_FAKE_LLM": "1", "FACTORY_FAKE_CONFIDENCE": "0.2"})
        summary = json.loads(r.stdout)
        all_cards = [json.loads(p.read_text(encoding="utf-8"))
                     for p in (pkg / "process" / "run-cards" / "t-judge").glob("*.json")]
        assert any(c["escalation"]["escalated"] and c["escalation"]["reason"] == "confidence_below_floor"
                   for c in all_cards), "low-confidence run must produce an escalated run card"

        # Kill switch: every run aborts
        (build / "KILL").write_text("", encoding="utf-8")
        r = run([PY, str(build / "agent" / "main.py"), "{}"], work, env={"FACTORY_FAKE_LLM": "1"})
        assert json.loads(r.stdout)["status"] == "killed"
        (build / "KILL").unlink()

        # No external effectors in the emitted runtime, by grep
        runtime_src = (build / "agent" / "main.py").read_text(encoding="utf-8")
        for forbidden in ("smtplib", "requests.post", "hubspot", "send_message", "sequence"):
            assert forbidden not in runtime_src, f"external effector surface found: {forbidden}"

    print("[OK] compiler test passed")


if __name__ == "__main__":
    test_compiler()

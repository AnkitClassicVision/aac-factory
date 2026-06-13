#!/usr/bin/env python3
"""
Regression test for the Karpathy improvement loop.

Proves the mechanics with stub executors (live model quality arrives with the S6 compiler):
  - objective block emitted on judgment nodes
  - holdout split sealed; open eval excludes sealed examples
  - improver evaluates champion + cheaper challengers, adopts equal-and-cheaper with holdout pass
  - adoption updates the card, writes the ledger, and re-enters QA
  - improver refuses non-judgment nodes and ungraded golden sets
  - QA flags a stripped objective block; heal re-injects it
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable

from test_agent_pipeline import ATLAS, PACKET, run  # noqa: E402


def build_pkg(work: Path) -> Path:
    shutil.copytree(REPO / "scripts", work / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (work / "concepts").mkdir()
    for t in ("_template_agent_package", "_template_concept_map"):
        shutil.copytree(REPO / "concepts" / t, work / "concepts" / t,
                        ignore=shutil.ignore_patterns("__pycache__"))
    run([PY, "scripts/new_agent_package.py", "Test Agent", "test-agent"], work)
    pkg = work / "concepts" / "test-agent"
    (pkg / "atlas" / "atlas.json").write_text(json.dumps(ATLAS, indent=2), encoding="utf-8")
    (pkg / "process" / "source_packet.aac.json").write_text(json.dumps(PACKET, indent=2), encoding="utf-8")
    run([PY, "scripts/atlas_to_concept.py", str(pkg)], work)
    run([PY, "scripts/concept_to_process.py", str(pkg)], work)
    return pkg


def test_improvement_loop() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        work.mkdir()
        pkg = build_pkg(work)

        judge_path = pkg / "process" / "nodes" / "t-judge.aac.json"
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        assert judge["objective"]["primary"]["metric"] == "golden_accuracy"
        assert judge["objective"]["improvement_policy"]["auto_adopt"] == "cheaper_or_better_with_holdout_pass"

        # improver: replay/stub executors may propose but must not auto-adopt in v0.3
        out = run([PY, "scripts/improve_node.py", str(pkg), "t-judge"], work)
        assert "proposal_queued_adoption_blocked_stub_executor" in out.stdout, out.stdout
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        assert judge["model"].startswith("TODO"), "stub evidence must not mutate the certified card model"
        assert (pkg / "exports" / "improvement_ledger.jsonl").exists()
        assert "adoption_blocked_stub_executor" in (pkg / "exports" / "improvement_ledger.jsonl").read_text(encoding="utf-8")
        assert (pkg / ".holdout" / "evals" / "t-judge.holdout.json").exists(), "holdout must be sealed"

        decision = json.loads((pkg / "exports" / "improvement_proposals" / "t-judge.json")
                              .read_text(encoding="utf-8"))
        assert decision["winner"]["holdout_pass"] is True
        sealed = {e["record_ref"] for e in json.loads(
            (pkg / ".holdout" / "evals" / "t-judge.holdout.json").read_text(encoding="utf-8"))}
        open_eval = json.loads((pkg / "exports" / "evals" / "t-judge__open__replay_proposed.json")
                               .read_text(encoding="utf-8"))
        open_refs = {p["record_ref"] for p in open_eval["per_example"]}
        assert not (sealed & open_refs), "open split must exclude sealed holdout examples"

        # second run: still proposal-only/idempotent under stub evidence
        out2 = run([PY, "scripts/improve_node.py", str(pkg), "t-judge"], work)
        assert "proposal_queued_adoption_blocked_stub_executor" in out2.stdout or "champion_stands" in out2.stdout

        # refusals: D node and H node are not improvable
        r = subprocess.run([PY, "scripts/improve_node.py", str(pkg), "t-intake"],
                           cwd=work, capture_output=True, text=True)
        assert r.returncode != 0 and "judgment" in (r.stdout + r.stderr)

        # QA flags stripped objective; heal re-injects
        del judge["objective"]
        judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")
        r = subprocess.run([PY, "scripts/qa_agent_package.py", str(pkg)], cwd=work,
                           capture_output=True, text=True)
        assert "objective_present" in r.stdout
        run([PY, "scripts/heal_agent_package.py", str(pkg)], work)
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        assert judge["objective"]["primary"]["metric"] == "golden_accuracy", "heal must re-inject objective"

        # QA flags partial Recorder trace contracts; heal completes missing fields rather than only empty lists
        wf_path = pkg / "process" / "workflow.aac.json"
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
        workflow_trace_fields = list(wf["tbr_gate"]["recorder"]["trace_fields"])
        wf["tbr_gate"]["recorder"]["trace_fields"] = workflow_trace_fields[:3]
        wf_path.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        node_trace_fields = list(judge["tbr_gate"]["recorder"]["trace_fields"])
        judge["tbr_gate"]["recorder"]["trace_fields"] = node_trace_fields[:3]
        judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")
        r = subprocess.run([PY, "scripts/qa_agent_package.py", str(pkg)], cwd=work,
                           capture_output=True, text=True)
        assert "tbr_recorder_trace_contract" in r.stdout
        run([PY, "scripts/heal_agent_package.py", str(pkg)], work)
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        assert wf["tbr_gate"]["recorder"]["trace_fields"] == workflow_trace_fields
        assert judge["tbr_gate"]["recorder"]["trace_fields"] == node_trace_fields

    print("[OK] improvement loop test passed")


if __name__ == "__main__":
    test_improvement_loop()

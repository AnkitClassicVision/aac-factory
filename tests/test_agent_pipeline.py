#!/usr/bin/env python3
"""
Regression test for the AAC agent-package pipeline.

Scaffolds a temp package, runs all five scripts, and asserts:
  - concept map lint passes and exports exist
  - crosswalk maps every ATLAS node
  - process cards generated with concept_ref/atlas_ref traceability
  - validator produces a readiness report with R0 PASS
  - combined exports (agent_map.mmd / agent_map.json) are written

Run:  python3 tests/test_agent_pipeline.py   (or pytest tests/test_agent_pipeline.py)
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

ATLAS = {
    "atlas_id": "atlas_test_agent",
    "subject": "Test Agent",
    "spine": "A test record moves from intake to a labeled outcome with human-graded proof.",
    "status": "building",
    "spine_path": ["N1", "N2", "N3"],
    "root_anchors": ["N10"],
    "clusters": [
        {"name": "Intake", "purpose": "What arrives.", "trunk_node": "N1", "members": ["N11", "N12"]},
        {"name": "Judgment", "purpose": "The bounded decision.", "trunk_node": "N2", "members": ["N13", "N14", "N15", "N16"]},
        {"name": "Proof", "purpose": "Human grading.", "trunk_node": "N3", "members": ["N17", "N18"]},
    ],
    "nodes": [
        {"id": "N1", "name": "Intake reality", "grouping": "Intake", "confidence": "Evidenced",
         "provenance": "measured", "status": "proven", "proof_slot": "live counts"},
        {"id": "N2", "name": "Bounded judgment", "grouping": "Judgment", "confidence": "Reasoned",
         "provenance": "user", "status": "building", "proof_slot": "golden grades"},
        {"id": "N3", "name": "Proof loop", "grouping": "Proof", "confidence": "Reasoned",
         "provenance": "user", "status": "building", "proof_slot": "review log"},
        {"id": "N10", "name": "Work object: test record", "grouping": "anchors", "confidence": "Evidenced",
         "provenance": "user", "status": "proven", "proof_slot": "record ids"},
        {"id": "N11", "name": "Volume baseline", "grouping": "Intake", "confidence": "Evidenced",
         "provenance": "measured", "status": "proven", "proof_slot": "weekly query"},
        {"id": "N12", "name": "Input pollution rate", "grouping": "Intake", "confidence": "Evidenced",
         "provenance": "measured", "status": "proven", "proof_slot": "weekly query"},
        {"id": "N13", "name": "Label vocabulary", "grouping": "Judgment", "confidence": "Reasoned",
         "provenance": "user", "status": "building", "proof_slot": "card enum"},
        {"id": "N14", "name": "Confidence floor", "grouping": "Judgment", "confidence": "Reasoned",
         "provenance": "inferred-model", "status": "open", "proof_slot": "calibration record"},
        {"id": "N15", "name": "Grounding source", "grouping": "Judgment", "confidence": "Reasoned",
         "provenance": "user", "status": "building", "proof_slot": "rules doc"},
        {"id": "N16", "name": "Refuse path", "grouping": "Judgment", "confidence": "Reasoned",
         "provenance": "user", "status": "building", "proof_slot": "refuse log"},
        {"id": "N17", "name": "Golden set", "grouping": "Proof", "confidence": "Reasoned",
         "provenance": "user", "status": "building", "proof_slot": "graded examples"},
        {"id": "N18", "name": "Run cards", "grouping": "Proof", "confidence": "Reasoned",
         "provenance": "user", "status": "building", "proof_slot": "per-run telemetry"},
    ],
    "edges": [
        {"id": "E1", "type": "feeds", "from": "N1", "to": "N2", "reason": "Measured intake defines judgment inputs.", "confidence": "E"},
        {"id": "E2", "type": "feeds", "from": "N2", "to": "N3", "reason": "Judgment outputs need graded proof.", "confidence": "R"},
        {"id": "E3", "type": "tension", "from": "N3", "to": "N2", "reason": "Single grader risk.", "confidence": "R"},
    ],
    "validation_backlog": ["N2: ungraded"],
    "shaky_tension_flags": [],
    "process_map": {"t-intake": "N1", "t-judge": "N2", "t-review": "N3"},
    "retrieval_sonar": {"aliases": ["test agent"], "project_terms": [], "owner": "test", "last_updated": "2026-06-09"},
    "interview": {"packet_ref": "process/source_packet.aac.json", "calibration_ref": "", "aac_version": "AAC 2.5 (test)"},
}

PACKET = {
    "card_type": "workflow",
    "aac_card_version": "2.5-draft",
    "workflow_id": "test-agent",
    "workflow_box": "test record in, labeled outcome out",
    "control_topology": "graph_directed",
    "value_mode": "augment",
    "trigger": "record created",
    "finished_state": "label set + queued for human, or refused with reason",
    "sinks": {"happy": "labeled + queued", "refuse": "held", "hard_refuse": "blocked"},
    "owners": {"process_owner": "TestOwner", "technical_owner": "TestTech",
               "reviewer": "TestReviewer", "residue_accepter": "TestOwner"},
    "max_lane": "draft",
    "director": "deterministic_router",
    "nodes": [
        {"node_id": "t-intake", "runtime_mode": "D", "purpose": "pull and dedupe records"},
        {"node_id": "t-judge", "runtime_mode": "C", "purpose": "label the record with confidence"},
        {"node_id": "t-review", "runtime_mode": "H", "purpose": "approve or reject the label"},
    ],
    "edges": [
        {"from": "t-intake", "to": "t-judge", "condition": "always"},
        {"from": "t-judge", "to": "t-review", "condition": "always"},
        {"from": "t-judge", "to": "refuse_sink", "condition": "low confidence"},
        {"from": "t-review", "to": "happy_sink", "condition": "approved"},
        {"from": "t-review", "to": "hard_refuse_sink", "condition": "violation"},
    ],
    "hard_refuse": ["no external writes in v1"],
    "cost_framing": {"cost_of_failure": "mislabeled records", "cost_of_inaction": "backlog grows",
                     "per_error_cost_band": "low"},
    "observability": {"run_card_required": True, "review_cadence": "weekly", "metrics": ["labeled_count", "refuse_rate"]},
    "residue": {"statement": "tone drift inside approved labels", "accepter": "TestOwner", "signed": False},
    "escalation_path": "refuse to owner",
    "kill_switch": "disable test-agent profile",
    "golden_set_ref": "process/evals/test-agent.golden.json",
    "golden_set": [
        {"record_ref": "r1", "input_summary": "clean record", "proposed": "label A", "grade": "right", "graded_by": "TestOwner", "corrected": ""},
        {"record_ref": "r2", "input_summary": "polluted record", "proposed": "refuse", "grade": "right", "graded_by": "TestOwner", "corrected": ""},
        {"record_ref": "r3", "input_summary": "edge case", "proposed": "label B", "grade": "edit", "graded_by": "TestOwner", "corrected": "label C"},
    ],
    "provenance": {"workflow_box": {"source": "confirmed", "ref": "test"}},
}


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"FAILED {' '.join(cmd)}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    return r


def test_pipeline() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "knowledge_repo"
        work.mkdir()
        shutil.copytree(REPO / "scripts", work / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (work / "concepts").mkdir()
        for t in ("_template_agent_package", "_template_concept_map"):
            shutil.copytree(REPO / "concepts" / t, work / "concepts" / t,
                            ignore=shutil.ignore_patterns("__pycache__"))

        run([PY, "scripts/new_agent_package.py", "Test Agent", "test-agent"], work)
        pkg = work / "concepts" / "test-agent"
        assert (pkg / "CLAUDE.md").exists() and (pkg / "test-agent_concept_map").exists()

        (pkg / "atlas" / "atlas.json").write_text(json.dumps(ATLAS, indent=2), encoding="utf-8")
        (pkg / "process" / "source_packet.aac.json").write_text(json.dumps(PACKET, indent=2), encoding="utf-8")

        out = run([PY, "scripts/atlas_to_concept.py", str(pkg)], work)
        assert "Lint passed" in out.stdout and "exports written" in out.stdout

        cw = json.loads((pkg / "atlas" / "crosswalk.json").read_text(encoding="utf-8"))
        assert len(cw["atlas_nodes"]) == len(ATLAS["nodes"]), "every ATLAS node must map"
        assert any("tension" in f for f in cw["flags"]), "tension edge must surface as a flag"

        run([PY, "scripts/concept_to_process.py", str(pkg)], work)
        for nid in ("t-intake", "t-judge", "t-review"):
            card = json.loads((pkg / "process" / "nodes" / f"{nid}.aac.json").read_text(encoding="utf-8"))
            assert card["atlas_ref"] == ATLAS["process_map"][nid]
            assert card["concept_ref"].startswith("node_"), f"{nid} concept_ref unresolved"
            assert card["telemetry"]["run_card_required"] is True
        judge = json.loads((pkg / "process" / "nodes" / "t-judge.aac.json").read_text(encoding="utf-8"))
        assert judge["prompt_ref"] == "process/prompts/t-judge.md"
        assert (pkg / "process" / "prompts" / "t-judge.md").exists()

        run([PY, "scripts/validate_agent_package.py", str(pkg), "--strict"], work)
        report = json.loads((pkg / "exports" / "readiness_report.json").read_text(encoding="utf-8"))
        assert report["ladder"]["R0_design_scaffold"]["pass"] is True
        assert report["ladder"]["R3_supervised_send"]["pass"] is False, "R3+ must stay a human gate"
        assert report["summary"]["golden_graded"] == 3

        run([PY, "scripts/export_agent_map.py", str(pkg)], work)
        mmd = (pkg / "exports" / "agent_map.mmd").read_text(encoding="utf-8")
        assert "subgraph CONCEPT" in mmd and "subgraph PROCESS" in mmd and ". serves ." in mmd
        unified = json.loads((pkg / "exports" / "agent_map.json").read_text(encoding="utf-8"))
        assert unified["spine"] == ATLAS["spine"]
        assert len(unified["process"]["node_cards"]) == 3

        # determinism: re-run synthesis, expect identical node/edge sets
        out2 = run([PY, "scripts/atlas_to_concept.py", str(pkg)], work)
        assert "Lint passed" in out2.stdout

        # full automated pipeline: QA + self-heal + human-over-the-loop queue
        out3 = run([PY, "scripts/run_pipeline.py", str(pkg)], work)
        assert "HUMAN-OVER-THE-LOOP QUEUE" in out3.stdout
        qa = json.loads((pkg / "exports" / "qa_report.json").read_text(encoding="utf-8"))
        assert qa["verdict"] in {"allow", "revise"}, f"unexpected QA verdict: {qa['verdict']}"
        assert (pkg / "exports" / "repair_proposals.json").exists()
        assert (pkg / ".holdout" / "scenarios" / "agent-package.yml").exists()
        judge = json.loads((pkg / "process" / "nodes" / "t-judge.aac.json").read_text(encoding="utf-8"))
        assert judge["supervision"]["mode"] == "human_over_loop"
        assert judge["supervision"]["inline_approval"] is False
        wf = json.loads((pkg / "process" / "workflow.aac.json").read_text(encoding="utf-8"))
        assert wf["automation_policy"]["stance"] == "human_over_loop"

        # leak gate: planted identifier must hard-block, removal must clear it
        leak = pkg / "process" / "leak_test.md"
        leak.write_text("contact me at someone@example.com", encoding="utf-8")
        r = subprocess.run([PY, "scripts/qa_agent_package.py", str(pkg)], cwd=work,
                           capture_output=True, text=True)
        assert "BLOCK" in r.stdout.upper(), "leak scan must block on planted email"
        leak.unlink()
        r = subprocess.run([PY, "scripts/qa_agent_package.py", str(pkg)], cwd=work,
                           capture_output=True, text=True)
        assert "BLOCK" not in r.stdout.splitlines()[0].upper()

    print("[OK] agent pipeline regression test passed")


def test_runtime_suggester() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import concept_to_process as ctp

    assert ctp.suggest_runtime("x-classify", "classify inbound records")["mode"] == "C"
    assert ctp.suggest_runtime("x-notify", "send the weekly email")["mode"] == "A"
    assert ctp.suggest_runtime("x-intake", "pull and dedupe records")["mode"] == "D"
    assert ctp.suggest_runtime("x-check", "approve or reject the label")["mode"] == "H"
    assert ctp.suggest_runtime("x-mystery", "handle the thing") is None
    print("[OK] runtime suggester test passed")


def test_run_card_contract() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import runcard

    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        rc = runcard.new_run_card(run_id="r1", workflow="t", node_id="t-judge", runtime_mode="C",
                                  ts_start="2026-06-09T00:00:00Z", ts_end="2026-06-09T00:00:01Z",
                                  input_ref="in", output_ref="out", source_refs=["s1"])
        errs = runcard.validate_run_card(rc)
        assert any("confidence" in e for e in errs), "C run card must demand confidence/model/prompt"
        rc.update({"confidence": 0.91, "requested_model": "m", "actual_model": "m",
                   "model_verified": True, "verification_source": "test",
                   "executor": "test", "adapter_version": "test", "prompt_version": "1.0.0"})
        assert runcard.validate_run_card(rc) == []
        assert rc["usage"]["tokens_in"] == "unknown" and rc["cost"]["tokens_in"] == "unknown"
        runcard.write_run_card(pkg, rc)
        assert (pkg / "process" / "run-cards" / "t-judge" / "r1.json").exists()
        rc2 = dict(rc, run_id="r2", refuse={"refused": True, "hard": True, "reason": "identity unclear"})
        runcard.write_run_card(pkg, rc2)
        assert list((pkg / "process" / "run-cards" / "_review_queue").glob("*.json")), \
            "refusals must land in the human-over-the-loop review queue"
    print("[OK] run-card contract test passed")


if __name__ == "__main__":
    test_pipeline()
    test_runtime_suggester()
    test_run_card_contract()

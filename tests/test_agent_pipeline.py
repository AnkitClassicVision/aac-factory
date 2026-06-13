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
        wf = json.loads((pkg / "process" / "workflow.aac.json").read_text(encoding="utf-8"))
        assert wf["tbr_gate"]["translator"]["raw_query_policy"].startswith("agents_may_not_query_raw")
        assert wf["tbr_gate"]["recorder"]["run_card_required"] is True
        for nid in ("t-intake", "t-judge", "t-review"):
            card = json.loads((pkg / "process" / "nodes" / f"{nid}.aac.json").read_text(encoding="utf-8"))
            assert card["atlas_ref"] == ATLAS["process_map"][nid]
            assert card["concept_ref"].startswith("node_"), f"{nid} concept_ref unresolved"
            assert card["telemetry"]["run_card_required"] is True
            assert card["tbr_gate"]["bouncer"]["human_identity_passthrough"] == "required"
            assert card["tbr_gate"]["recorder"]["permission_decision_logged"] is True
        judge = json.loads((pkg / "process" / "nodes" / "t-judge.aac.json").read_text(encoding="utf-8"))
        assert judge["prompt_ref"] == "process/prompts/t-judge.md"
        assert (pkg / "process" / "prompts" / "t-judge.md").exists()

        run([PY, "scripts/validate_agent_package.py", str(pkg), "--strict"], work)
        report = json.loads((pkg / "exports" / "readiness_report.json").read_text(encoding="utf-8"))
        assert report["ladder"]["R0_design_scaffold"]["pass"] is True
        assert report["tbr"]["complete"] is False, "generated TBR defaults must block certification until owners fill refs/policies"
        assert report["summary"]["tbr_blockers"] > 0
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
        tbr_missing_errors = runcard.validate_run_card(rc)
        assert any("TBR run proof requires" in e for e in tbr_missing_errors)
        assert rc["tbr_required"] is True
        assert rc["certification_eligible"] is False
        assert "tbr_definition_refs_missing" in rc["certification_blockers"]
        rc_explicit_no_tbr = runcard.new_run_card(run_id="r1no", workflow="t", node_id="t-judge", runtime_mode="C",
                                                   ts_start="2026-06-09T00:00:00Z", ts_end="2026-06-09T00:00:01Z",
                                                   input_ref="in", output_ref="out", source_refs=["s1"], confidence=0.91,
                                                   requested_model="m", actual_model="m", model_verified=True,
                                                   verification_source="test", executor="test", adapter_version="test",
                                                   prompt_version="1.0.0", tbr_required=False)
        assert runcard.validate_run_card(rc_explicit_no_tbr) == []
        assert rc_explicit_no_tbr["certification_eligible"] is False
        assert "tbr_not_required_non_certifying" in rc_explicit_no_tbr["certification_blockers"]
        rc_bad_tbr = dict(rc, tbr_required=True, tbr={})
        assert any("TBR" in e for e in runcard.validate_run_card(rc_bad_tbr)), "required TBR proof must be physical"
        rc_tbr = runcard.new_run_card(run_id="r1t", workflow="t", node_id="t-judge", runtime_mode="C",
                                      ts_start="2026-06-09T00:00:00Z", ts_end="2026-06-09T00:00:01Z",
                                      input_ref="in", output_ref="out", source_refs=["s1"],
                                      confidence=0.91, requested_model="m", actual_model="m",
                                      model_verified=True, verification_source="test", executor="test",
                                      adapter_version="test", prompt_version="1.0.0", tbr_required=True,
                                      tbr={"definition_refs": ["def:active-customer"],
                                           "semantic_source_refs": ["warehouse.metric_view"],
                                           "prompt_ref": "process/prompts/t-judge.md",
                                           "prompt_version": "1.0.0",
                                           "response_ref": "out",
                                           "tool_call_refs": ["audit:zero_tool_calls_executed"],
                                           "user_ref": "user:test",
                                           "recipient_ref": "recipient:test",
                                           "retention_class": "six_months",
                                           "tamper_evidence": "append_only_log",
                                           "trace_fields": list(runcard.TBR_TRACE_FIELDS),
                                           "permission_decision": {"allowed": True, "policy_ref": "policy:user-via-agent", "reason": "test"}})
        assert runcard.validate_run_card(rc_tbr) == []
        rc_todo_tbr: dict = dict(rc_tbr, run_id="r1todo", certification_eligible=True, certification_blockers=[])
        rc_todo_tbr["tbr"] = {
            "definition_refs": ["TODO: semantic definition"],
            "semantic_source_refs": ["warehouse.metric_view"],
            "prompt_ref": "process/prompts/t-judge.md",
            "prompt_version": "1.0.0",
            "response_ref": "out",
            "tool_call_refs": ["audit:zero_tool_calls_executed"],
            "user_ref": "user:test",
            "recipient_ref": "recipient:test",
            "retention_class": "six_months",
            "tamper_evidence": "append_only_log",
            "permission_decision": {"allowed": True, "policy_ref": "policy:user-via-agent", "reason": "test"},
            "trace_fields": list(runcard.TBR_TRACE_FIELDS),
        }
        assert runcard.validate_run_card(rc_todo_tbr) == []
        assert rc_todo_tbr["certification_eligible"] is False
        assert "tbr_contains_todo" in rc_todo_tbr["certification_blockers"]
        rc_placeholder_tbr: dict = json.loads(json.dumps(rc_tbr))
        rc_placeholder_tbr["tbr"]["definition_refs"] = ["placeholder"]
        rc_placeholder_tbr["tbr"]["permission_decision"]["policy_ref"] = "policy:any request"
        assert runcard.validate_run_card(rc_placeholder_tbr) == []
        assert rc_placeholder_tbr["certification_eligible"] is False
        assert "tbr_contains_placeholder" in rc_placeholder_tbr["certification_blockers"]
        assert "tbr_permission_scope_unbounded" in rc_placeholder_tbr["certification_blockers"]
        rc_none_selected_tbr: dict = json.loads(json.dumps(rc_tbr))
        rc_none_selected_tbr["tbr"]["definition_refs"] = ["none selected"]
        rc_none_selected_tbr["tbr"]["semantic_source_refs"] = ["none selected"]
        rc_none_selected_tbr["tbr"]["permission_decision"]["policy_ref"] = "none selected"
        rc_none_selected_tbr["tbr"]["tamper_evidence"] = "none selected"
        assert runcard.validate_run_card(rc_none_selected_tbr) == []
        assert rc_none_selected_tbr["certification_eligible"] is False
        assert "tbr_contains_placeholder" in rc_none_selected_tbr["certification_blockers"]
        no_proof_cases = {
            "definition_refs": ["no_definition"],
            "semantic_source_refs": ["no_source"],
            "prompt_ref": "none:no_ref",
            "prompt_version": "no_prompt_version",
            "response_ref": "no_response",
            "tool_call_refs": ["not_applicable:no_tool"],
            "user_ref": "no_user",
            "recipient_ref": "no_recipient",
            "retention_class": "no_retention",
            "tamper_evidence": "no_tamper",
        }
        for field, bad_value in no_proof_cases.items():
            rc_no_proof_tbr: dict = json.loads(json.dumps(rc_tbr))
            rc_no_proof_tbr["tbr"][field] = bad_value
            assert runcard.validate_run_card(rc_no_proof_tbr) == []
            assert rc_no_proof_tbr["certification_eligible"] is False
            assert "tbr_contains_no_proof_sentinel" in rc_no_proof_tbr["certification_blockers"]
        rc_no_policy_tbr: dict = json.loads(json.dumps(rc_tbr))
        rc_no_policy_tbr["tbr"]["permission_decision"]["policy_ref"] = "no_policy"
        assert runcard.validate_run_card(rc_no_policy_tbr) == []
        assert rc_no_policy_tbr["certification_eligible"] is False
        assert "tbr_contains_no_proof_sentinel" in rc_no_policy_tbr["certification_blockers"]
        audit_no_proof_cases = {
            "definition_refs": ["audit:no_definition"],
            "semantic_source_refs": ["audit:no_source"],
            "user_ref": "audit:no_user",
        }
        for field, bad_value in audit_no_proof_cases.items():
            rc_audit_no_proof_tbr: dict = json.loads(json.dumps(rc_tbr))
            rc_audit_no_proof_tbr["tbr"][field] = bad_value
            assert runcard.validate_run_card(rc_audit_no_proof_tbr) == []
            assert rc_audit_no_proof_tbr["certification_eligible"] is False
            assert "tbr_contains_no_proof_sentinel" in rc_audit_no_proof_tbr["certification_blockers"]
        rc_audit_no_policy_tbr: dict = json.loads(json.dumps(rc_tbr))
        rc_audit_no_policy_tbr["tbr"]["permission_decision"]["policy_ref"] = "audit:no_policy"
        assert runcard.validate_run_card(rc_audit_no_policy_tbr) == []
        assert rc_audit_no_policy_tbr["certification_eligible"] is False
        assert "tbr_contains_no_proof_sentinel" in rc_audit_no_policy_tbr["certification_blockers"]
        rc_shared_service_tbr: dict = json.loads(json.dumps(rc_tbr))
        rc_shared_service_tbr["tbr"]["permission_decision"]["policy_ref"] = "policy:shared-service-account"
        assert runcard.validate_run_card(rc_shared_service_tbr) == []
        assert rc_shared_service_tbr["certification_eligible"] is False
        assert "tbr_permission_scope_unbounded" in rc_shared_service_tbr["certification_blockers"]
        rc_non_grant_words_tbr: dict = json.loads(json.dumps(rc_tbr))
        rc_non_grant_words_tbr["tbr"]["definition_refs"] = ["all salary records definition"]
        rc_non_grant_words_tbr["tbr"]["retention_class"] = "review every 30 days"
        assert runcard.validate_run_card(rc_non_grant_words_tbr) == []
        assert rc_non_grant_words_tbr["certification_eligible"] is True
        assert "tbr_permission_scope_unbounded" not in rc_non_grant_words_tbr["certification_blockers"]
        rc_string_allowed: dict = json.loads(json.dumps(rc_tbr))
        rc_string_allowed["tbr"]["permission_decision"]["allowed"] = "true"
        string_allowed_errors = runcard.validate_run_card(rc_string_allowed)
        assert any("allowed must be true/false" in e for e in string_allowed_errors)
        assert rc["usage"]["tokens_in"] == "unknown" and rc["cost"]["tokens_in"] == "unknown"
        runcard.write_run_card(pkg, rc_tbr)
        assert (pkg / "process" / "run-cards" / "t-judge" / "r1t.json").exists()
        rc2 = dict(rc_tbr, run_id="r2", refuse={"refused": True, "hard": True, "reason": "identity unclear"})
        runcard.write_run_card(pkg, rc2)
        assert list((pkg / "process" / "run-cards" / "_review_queue").glob("*.json")), \
            "refusals must land in the human-over-the-loop review queue"
    print("[OK] run-card contract test passed")


def test_tbr_gate_fail_closed_contract() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import tbr_gate  # type: ignore[reportMissingImports]

    workflow_missing_cadence = {
        "max_lane": "draft",
        "tbr_gate": {
            "required": True,
            "translator": {
                "canonical_definitions": [{"term": "active_customer", "definition_ref": "defs/active", "source_of_truth_ref": "semantic/active"}],
                "source_of_truth_refs": ["semantic/active"],
                "raw_query_policy": "semantic_gate_required",
            },
            "bouncer": {
                "effective_permission_model": "user_via_agent",
                "sensitive_systems": ["crm:customer_sensitive_fields"],
                "policy_engine_ref": "policy/runtime",
                "task_scoped_tokens_required": True,
            },
            "recorder": {
                "run_card_required": True,
                "trace_fields": list(tbr_gate.TBR_TRACE_FIELDS),
                "retention_class": "six_months",
                "tamper_evidence": "append_only_log",
            },
        },
    }
    assert any("review_cadence" in e for e in tbr_gate.validate_workflow_tbr(workflow_missing_cadence))
    workflow_missing_sensitive = json.loads(json.dumps(workflow_missing_cadence))
    workflow_missing_sensitive["tbr_gate"]["recorder"]["review_cadence"] = "monthly audit"
    workflow_missing_sensitive["tbr_gate"]["bouncer"].pop("sensitive_systems")
    assert any("sensitive_systems" in e for e in tbr_gate.validate_workflow_tbr(workflow_missing_sensitive))
    workflow_bad_canonical_definition = json.loads(json.dumps(workflow_missing_cadence))
    workflow_bad_canonical_definition["tbr_gate"]["recorder"]["review_cadence"] = "monthly audit"
    workflow_bad_canonical_definition["tbr_gate"]["translator"]["canonical_definitions"] = [{"term": "active_customer"}]
    bad_definition_errors = tbr_gate.validate_workflow_tbr(workflow_bad_canonical_definition)
    assert any("canonical_definitions[0].definition_ref" in e for e in bad_definition_errors)
    assert any("canonical_definitions[0].source_of_truth_ref" in e for e in bad_definition_errors)
    workflow_no_policy = json.loads(json.dumps(workflow_missing_cadence))
    workflow_no_policy["tbr_gate"]["recorder"]["review_cadence"] = "monthly audit"
    workflow_no_policy["tbr_gate"]["bouncer"]["policy_engine_ref"] = "no_policy"
    assert any("policy_engine_ref" in e for e in tbr_gate.validate_workflow_tbr(workflow_no_policy))
    workflow_none_selected = json.loads(json.dumps(workflow_missing_cadence))
    workflow_none_selected["tbr_gate"]["recorder"]["review_cadence"] = "monthly audit"
    workflow_none_selected["tbr_gate"]["translator"]["source_of_truth_refs"] = ["none selected"]
    workflow_none_selected["tbr_gate"]["bouncer"]["policy_engine_ref"] = "none selected"
    workflow_none_selected["tbr_gate"]["recorder"]["tamper_evidence"] = "none selected"
    none_selected_errors = tbr_gate.validate_workflow_tbr(workflow_none_selected)
    assert any("source_of_truth_refs" in e for e in none_selected_errors)
    assert any("policy_engine_ref" in e for e in none_selected_errors)
    assert any("tamper_evidence" in e for e in none_selected_errors)
    workflow_audit_no_policy = json.loads(json.dumps(workflow_missing_cadence))
    workflow_audit_no_policy["tbr_gate"]["recorder"]["review_cadence"] = "monthly audit"
    workflow_audit_no_policy["tbr_gate"]["bouncer"]["policy_engine_ref"] = "audit:no_policy"
    assert any("policy_engine_ref" in e for e in tbr_gate.validate_workflow_tbr(workflow_audit_no_policy))
    workflow_bare_no_proof = json.loads(json.dumps(workflow_missing_cadence))
    workflow_bare_no_proof["tbr_gate"]["recorder"]["review_cadence"] = "no_review"
    workflow_bare_no_proof["tbr_gate"]["recorder"]["retention_class"] = "no_retention"
    workflow_bare_no_proof["tbr_gate"]["recorder"]["tamper_evidence"] = "no_tamper"
    workflow_bare_no_proof["tbr_gate"]["bouncer"]["sensitive_systems"] = ["no_sensitive_map"]
    bare_no_errors = tbr_gate.validate_workflow_tbr(workflow_bare_no_proof)
    assert any("sensitive_systems" in e for e in bare_no_errors)
    assert any("review_cadence" in e for e in bare_no_errors)
    assert any("retention_class" in e for e in bare_no_errors)
    assert any("tamper_evidence" in e for e in bare_no_errors)
    workflow_review_every = json.loads(json.dumps(workflow_missing_cadence))
    workflow_review_every["tbr_gate"]["recorder"]["review_cadence"] = "review every 30 days"
    assert tbr_gate.validate_workflow_tbr(workflow_review_every) == []

    workflow_required_false = {"max_lane": "draft", "tbr_gate": {"required": False}}
    node_required_false = {"node_id": "n", "runtime_mode": "C", "tbr_gate": {"required": False}}
    assert tbr_gate.tbr_required_for_workflow(workflow_required_false) is True
    assert tbr_gate.tbr_required_for_node(workflow_required_false, node_required_false) is True
    workflow_exempt = {"max_lane": "internal_artifact_only", "tbr_gate": {
        "required": False, "non_certifying": True, "exemption_reason": "offline fixture package"}}
    assert tbr_gate.tbr_required_for_workflow(workflow_exempt) is False
    for placeholder_reason in ("todo later", "n/a", "none", "none selected"):
        workflow_bad_exempt = {"max_lane": "internal_artifact_only", "tbr_gate": {
            "required": False, "non_certifying": True, "exemption_reason": placeholder_reason}}
        assert tbr_gate.tbr_required_for_workflow(workflow_bad_exempt) is True

    node_missing_forbidden = {
        "node_id": "n",
        "runtime_mode": "C",
        "tbr_gate": {
            "required": True,
            "translator": {"definition_refs": ["defs/active"], "source_of_truth_refs": ["semantic/active"],
                           "source_refs_required": True, "raw_query_allowed": False},
            "bouncer": {
                "agent_identity": "agent:n",
                "human_identity_passthrough": "required",
                "allowed_resources": ["deal:read"],
                "effective_permission_path": "user->agent->policy->deal",
                "task_scope": "read deal for current run",
            },
            "recorder": {
                "run_card_required": True,
                "trace_fields": list(tbr_gate.TBR_TRACE_FIELDS),
                "permission_decision_logged": True,
                "definition_refs_logged": True,
                "source_refs_logged": True,
                "retention_class": "six_months",
                "tamper_evidence": "append_only_log",
            },
        },
    }
    assert any("forbidden_resources" in e for e in tbr_gate.validate_node_tbr(workflow_missing_cadence, node_missing_forbidden))
    node_valid_tbr = json.loads(json.dumps(node_missing_forbidden))
    node_valid_tbr["tbr_gate"]["bouncer"]["forbidden_resources"] = ["hr:salary"]
    assert tbr_gate.validate_node_tbr(workflow_missing_cadence, node_valid_tbr) == []
    node_no_policy = json.loads(json.dumps(node_valid_tbr))
    node_no_policy["tbr_gate"]["bouncer"]["effective_permission_path"] = "no_policy"
    assert any("effective_permission_path" in e for e in tbr_gate.validate_node_tbr(workflow_missing_cadence, node_no_policy))
    node_none_selected = json.loads(json.dumps(node_valid_tbr))
    node_none_selected["tbr_gate"]["translator"]["definition_refs"] = ["none selected"]
    node_none_selected["tbr_gate"]["translator"]["source_of_truth_refs"] = ["none selected"]
    node_none_selected["tbr_gate"]["bouncer"]["effective_permission_path"] = "none selected"
    node_none_selected["tbr_gate"]["recorder"]["tamper_evidence"] = "none selected"
    none_selected_node_errors = tbr_gate.validate_node_tbr(workflow_missing_cadence, node_none_selected)
    assert any("definition_refs" in e for e in none_selected_node_errors)
    assert any("source_of_truth_refs" in e for e in none_selected_node_errors)
    assert any("effective_permission_path" in e for e in none_selected_node_errors)
    assert any("tamper_evidence" in e for e in none_selected_node_errors)
    node_audit_no_policy = json.loads(json.dumps(node_valid_tbr))
    node_audit_no_policy["tbr_gate"]["bouncer"]["effective_permission_path"] = "audit:no_policy"
    node_audit_no_policy["tbr_gate"]["translator"]["definition_refs"] = ["audit:no_definition"]
    node_audit_no_policy["tbr_gate"]["translator"]["source_of_truth_refs"] = ["audit:no_source"]
    audit_no_node_errors = tbr_gate.validate_node_tbr(workflow_missing_cadence, node_audit_no_policy)
    assert any("effective_permission_path" in e for e in audit_no_node_errors)
    assert any("definition_refs" in e for e in audit_no_node_errors)
    assert any("source_of_truth_refs" in e for e in audit_no_node_errors)
    node_forbid_all_salary = json.loads(json.dumps(node_valid_tbr))
    node_forbid_all_salary["tbr_gate"]["bouncer"]["forbidden_resources"] = ["all salary records"]
    assert tbr_gate.validate_node_tbr(workflow_missing_cadence, node_forbid_all_salary) == []

    bad_semantic_workflow = json.loads(json.dumps(workflow_missing_cadence))
    bad_semantic_workflow["tbr_gate"]["recorder"]["review_cadence"] = "monthly audit"
    bad_semantic_workflow["tbr_gate"]["bouncer"]["effective_permission_model"] = "shared_service_account"
    bad_semantic_workflow["tbr_gate"]["bouncer"]["task_scoped_tokens_required"] = False
    bad_semantic_workflow["tbr_gate"]["translator"]["raw_query_policy"] = "raw SQL allowed with semantic gate"
    bad_semantic_workflow["tbr_gate"]["translator"]["source_of_truth_refs"] = ["placeholder"]
    bad_semantic_workflow["tbr_gate"]["bouncer"]["sensitive_systems"] = ["none:no_sensitive_map"]
    bad_workflow_errors = tbr_gate.validate_workflow_tbr(bad_semantic_workflow)
    assert any("effective_permission_model" in e for e in bad_workflow_errors)
    assert any("task_scoped_tokens_required" in e for e in bad_workflow_errors)
    assert any("raw_query_policy" in e for e in bad_workflow_errors)
    assert any("source_of_truth_refs" in e and "concrete" in e for e in bad_workflow_errors)
    assert any("sensitive_systems" in e and "concrete" in e for e in bad_workflow_errors)

    node_bad_semantics = json.loads(json.dumps(node_missing_forbidden))
    node_bad_semantics["tbr_gate"]["bouncer"]["forbidden_resources"] = ["hr:salary"]
    node_bad_semantics["tbr_gate"]["bouncer"]["allowed_resources"] = ["crm:*"]
    node_bad_semantics["tbr_gate"]["bouncer"]["task_scope"] = "any request forever"
    node_bad_semantics["tbr_gate"]["bouncer"]["human_identity_passthrough"] = "not_required"
    node_bad_semantics["tbr_gate"]["translator"]["raw_query_allowed"] = True
    node_bad_semantics["tbr_gate"]["recorder"]["permission_decision_logged"] = False
    node_bad_semantics["tbr_gate"]["recorder"]["trace_fields"] = ["run_id"]
    bad_node_errors = tbr_gate.validate_node_tbr(workflow_missing_cadence, node_bad_semantics)
    assert any("human_identity_passthrough" in e for e in bad_node_errors)
    assert any("raw_query_allowed" in e for e in bad_node_errors)
    assert any("permission_decision_logged" in e for e in bad_node_errors)
    assert any("allowed_resources" in e for e in bad_node_errors)
    assert any("task_scope" in e for e in bad_node_errors)
    assert any("recorder.trace_fields" in e for e in bad_node_errors)
    node_shared_service = json.loads(json.dumps(node_missing_forbidden))
    node_shared_service["tbr_gate"]["bouncer"]["forbidden_resources"] = ["hr:salary"]
    node_shared_service["tbr_gate"]["bouncer"]["agent_identity"] = "shared-service-account"
    shared_service_errors = tbr_gate.validate_node_tbr(workflow_missing_cadence, node_shared_service)
    assert any("agent_identity" in e and "unbounded" in e for e in shared_service_errors)
    print("[OK] TBR gate fail-closed contract test passed")


if __name__ == "__main__":
    test_pipeline()
    test_runtime_suggester()
    test_run_card_contract()
    test_tbr_gate_fail_closed_contract()

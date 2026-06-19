#!/usr/bin/env python3
"""v0.3 runtime-truth regressions from the Scott web-research triage trial."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

from test_compiler import make_workflow_conditions_parseable, run  # noqa: E402
from test_improvement_loop import build_pkg  # noqa: E402


def _compileable_pkg(work: Path) -> Path:
    pkg = build_pkg(work)
    make_workflow_conditions_parseable(pkg)
    judge_path = pkg / "process" / "nodes" / "t-judge.aac.json"
    judge = json.loads(judge_path.read_text(encoding="utf-8"))
    judge["model"] = "claude-sonnet-4-6"
    judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")
    return pkg


def _implement_d_handler(build: Path) -> None:
    (build / "agent" / "handlers" / "t_intake.py").write_text(
        "def run(ctx):\n    return {**ctx, 'records': [1, 2], 'decision': 'ingested', 'confidence': 1.0}\n",
        encoding="utf-8")


def test_gate_grammar() -> None:
    import gate_grammar
    assert gate_grammar.eval_condition('decision == "include"', {"decision": "include"})
    ok, reason = gate_grammar.execute_gate(
        'required_if(decision == "include", section)', {"decision": "include", "section": None})
    assert not ok and "failed" in reason
    ok, _ = gate_grammar.execute_gate(
        'regex_absent(rationale, research_filler)', {"rationale": "Fable launched"})
    assert ok
    ok, _ = gate_grammar.execute_gate(
        'regex_absent(rationale, research_filler)', {"rationale": "No new development on this item"})
    assert not ok


def test_prose_gate_fails_compile_and_required_if_runs() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        work.mkdir()
        pkg = _compileable_pkg(work)
        judge_path = pkg / "process" / "nodes" / "t-judge.aac.json"
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        judge["gates"]["output"]["checks"] = ["section present when decision=include"]
        judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")
        r = run([PY, "scripts/compile_agent.py", str(pkg)], work)
        assert r.returncode != 0 and "not executable" in r.stdout + r.stderr

        judge["gates"]["output"]["checks"] = ['required_if(decision == "include", section)']
        judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")
        r = run([PY, "scripts/compile_agent.py", str(pkg)], work)
        assert r.returncode == 0, r.stdout + r.stderr
        build = pkg / "build"
        _implement_d_handler(build)
        r = run([PY, str(build / "agent" / "main.py"), "{}"], work,
                env={"FACTORY_FAKE_LLM": "1", "FACTORY_FAKE_DECISION": "include",
                     "FACTORY_FAKE_SECTION": "__null__"})
        assert r.returncode == 0, r.stdout + r.stderr
        summary = json.loads(r.stdout)
        assert summary["outcome"] == "refuse_sink", summary
        cards = sorted((pkg / "process" / "run-cards" / "t-judge").glob("*.json"))
        card = json.loads(cards[-1].read_text(encoding="utf-8"))
        assert "output_gate_failed" in card["certification_blockers"]
        assert card["usage"]["tokens_in"] == "unknown" and card["cost"]["tokens_in"] == "unknown"


def test_claude_headless_model_truth_and_local_endpoint() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "llm_adapters_test", REPO / "scripts" / "runtime_templates" / "llm_adapters.py")
    llm = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(llm)

    with tempfile.TemporaryDirectory() as td:
        bin_dir = Path(td)
        args_path = bin_dir / "args.json"
        fake = bin_dir / "claude"
        fake.write_text(
            f"#!/usr/bin/env python3\nimport json, sys, pathlib\n"
            f"pathlib.Path({str(args_path)!r}).write_text(json.dumps(sys.argv))\n"
            "print(json.dumps({'result':'{\"decision\":\"include\",\"confidence\":0.99}',"
            "'model':'claude-sonnet-4-6','usage':{'input_tokens':3,'output_tokens':4}}))\n",
            encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}:{old_path}"
        try:
            result = llm.complete_result({"harness": "claude-code-headless"}, "claude-sonnet-4-6", "sys", "user")
        finally:
            os.environ["PATH"] = old_path
        args = json.loads(args_path.read_text(encoding="utf-8"))
        assert "--model" in args and "claude-sonnet-4-6" in args
        assert result["model_verified"] is True
        assert result["usage"]["tokens_in"] == 3 and result["usage"]["source"] == "cli_json"

    calls: list[str] = []
    def fake_post(url, headers, body, timeout=120):
        calls.append(url)
        return {"model": body["model"], "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                "choices": [{"message": {"content": "{\"decision\":\"include\",\"confidence\":0.9}"}}]}
    llm._post_json = fake_post
    result = llm.complete_result({"harness": "local-openai-compatible", "base_url": "http://localhost:8000/v1"},
                                 "mlx-community/test", "sys", "user")
    assert calls == ["http://localhost:8000/v1/chat/completions"]
    assert result["executor"] == "local-openai-compatible" and result["model_verified"] is True


def test_hermes_cron_and_batch_parent_child_cards() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        work.mkdir()
        pkg = _compileable_pkg(work)
        wf_path = pkg / "process" / "workflow.aac.json"
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
        wf["runtime_target"] = "hermes-cron"
        wf_path.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        judge_path = pkg / "process" / "nodes" / "t-judge.aac.json"
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        judge["gates"]["output"]["checks"] = ['required_if(decision == "include", section)',
                                                "regex_absent(rationale, research_filler)"]
        judge_path.write_text(json.dumps(judge, indent=2), encoding="utf-8")

        r = run([PY, "scripts/compile_agent.py", str(pkg)], work)
        assert r.returncode == 0, r.stdout + r.stderr
        build = pkg / "build"
        assert (build / "deploy" / "hermes_cron.json").exists()
        dry = run([PY, str(build / "deploy" / "hermes_no_agent_wrapper.py"), "--dry-run"], work)
        assert dry.returncode == 0 and json.loads(dry.stdout)["status"] == "dry_run_ok"
        _implement_d_handler(build)
        payload = json.dumps({"candidates": [{"id": 1}, {"id": 2}]})
        r = run([PY, str(build / "agent" / "main.py"), payload], work,
                env={"FACTORY_FAKE_LLM": "1", "FACTORY_FAKE_DECISION": "include",
                     "FACTORY_FAKE_SECTION": "platform_watch",
                     "FACTORY_FAKE_RATIONALE": "Fable 5 launch"})
        assert r.returncode == 0, r.stdout + r.stderr
        parent = json.loads(r.stdout)
        assert parent["run_kind"] == "batch_parent" and parent["candidate_count"] == 2
        child_cards = [json.loads(p.read_text(encoding="utf-8"))
                       for p in (pkg / "process" / "run-cards" / "t-judge").glob("*.json")]
        assert len(child_cards) >= 2
        assert all(c["run_kind"] == "batch_child" and c["parent_run_id"] == parent["run_id"]
                   for c in child_cards[-2:])


if __name__ == "__main__":
    test_gate_grammar()
    test_prose_gate_fails_compile_and_required_if_runs()
    test_claude_headless_model_truth_and_local_endpoint()
    test_hermes_cron_and_batch_parent_child_cards()
    print("[OK] runtime truth tests passed")

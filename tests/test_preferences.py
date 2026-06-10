#!/usr/bin/env python3
"""Preferences flow: factory.config.json drives harness, auth mode, default model,
and runtime_target in generated cards; per-card values stay overridable."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from test_improvement_loop import build_pkg  # noqa: E402


def test_preferences_flow() -> None:
    cfg = json.loads((REPO / "factory.config.json").read_text(encoding="utf-8"))
    assert cfg["execution_preferences"]["judgment_harness"] == "claude-code-headless"

    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        work.mkdir()
        pkg = build_pkg(work)  # copies scripts + factory.config.json? build_pkg copies scripts only
        judge = json.loads((pkg / "process" / "nodes" / "t-judge.aac.json").read_text(encoding="utf-8"))
        # build_pkg's temp repo has no factory.config.json -> built-in defaults apply
        assert judge["execution"]["harness"] == "anthropic-api", "defaults without config"

        # now install a config and regenerate with --force: preferences must flow
        (work / "factory.config.json").write_text(json.dumps({
            "execution_preferences": {"judgment_harness": "claude-code-headless", "auth_mode": "oauth-cli"},
            "model_preferences": {"default_judgment_model": "claude-sonnet-4-6", "allow_local_models": True,
                                  "local": {"provider": "ollama", "endpoint": "http://localhost:11434",
                                            "models": ["llama3.1"]}},
            "deploy_preferences": {"runtime_target": "local-cron"},
        }, indent=2), encoding="utf-8")
        import subprocess
        r = subprocess.run([sys.executable, "scripts/concept_to_process.py", str(pkg), "--force"],
                           cwd=work, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        judge = json.loads((pkg / "process" / "nodes" / "t-judge.aac.json").read_text(encoding="utf-8"))
        assert judge["execution"]["harness"] == "claude-code-headless"
        assert judge["execution"]["auth_mode"] == "oauth-cli"
        assert "local-model" in judge["execution"]["harness_options"]
        assert judge["model"] == "claude-sonnet-4-6", "default judgment model from preferences"
        wf = json.loads((pkg / "process" / "workflow.aac.json").read_text(encoding="utf-8"))
        assert wf["runtime_target"] == "local-cron"

        # local model joins the improvement ladder at cost_rank 0
        sys.path.insert(0, str(work / "scripts"))
        import importlib
        import factory_config
        importlib.reload(factory_config)
        ladder = factory_config.model_ladder()
        assert any(m["id"] == "ollama/llama3.1" and m["cost_rank"] == 0 for m in ladder)

    print("[OK] preferences flow test passed")




def test_model_scan() -> None:
    """Offline scan: env-key detection + catalog filtering + config seeding precedence."""
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        (work / "scripts").mkdir(parents=True)
        for f in ("scan_models.py", "model_catalog.json", "factory_config.py"):
            (work / "scripts" / f).write_text((REPO / "scripts" / f).read_text(encoding="utf-8"), encoding="utf-8")
        env = {k: v for k, v in __import__("os").environ.items()
               if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                            "MISTRAL_API_KEY", "DEEPSEEK_API_KEY"}}
        env.update({"DEEPSEEK_API_KEY": "test-key", "PATH": "/usr/bin:/bin"})  # no claude CLI, no other keys
        r = subprocess.run([sys.executable, "scripts/scan_models.py", "--write-config"],
                           cwd=work, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        scan = json.loads((work / ".factory-scan.json").read_text(encoding="utf-8"))
        assert scan["providers"]["deepseek"]["available"] is True
        assert scan["providers"]["openai"]["available"] is False
        ids = [m["id"] for m in scan["ladder"]]
        assert "deepseek-chat" in ids and not any(i.startswith("gpt-") for i in ids)
        cfg = json.loads((work / "factory.config.json").read_text(encoding="utf-8"))
        assert cfg["model_preferences"]["ladder"] == scan["ladder"], "scan must seed empty config"
        # second write must NOT clobber the now-set ladder
        r2 = subprocess.run([sys.executable, "scripts/scan_models.py", "--write-config"],
                            cwd=work, capture_output=True, text=True, env=env)
        assert "left untouched" in r2.stdout
    print("[OK] model scan test passed")


if __name__ == "__main__":
    test_preferences_flow()
    test_model_scan()

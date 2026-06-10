#!/usr/bin/env python3
"""
One-command AAC agent pipeline: automated end to end, human OVER the loop.

  synthesize -> cards -> validate -> QA (dark-factory) -> self-heal loop -> re-validate
  -> combined export -> consolidated status with the human queue.

The ONLY stops are the named highest-risk gates (golden grading queue, residue signing,
R3/R4 promotion, irreversible/external actions). Everything else runs and self-heals.

Usage: python scripts/run_pipeline.py <package_dir> [--skip-synth] [--max-heal N]
Exit:  0 = allow/revise (work continues; human queue listed)   1 = block
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run(script: str, pkg: Path, *extra: str) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, str(SCRIPTS / script), str(pkg), *extra],
                       capture_output=True, text=True)
    return r


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: python scripts/run_pipeline.py <package_dir> [--skip-synth] [--max-heal N]")
    pkg = Path(args[0]).resolve()
    skip_synth = "--skip-synth" in sys.argv
    max_heal = 3
    if "--max-heal" in sys.argv:
        max_heal = int(sys.argv[sys.argv.index("--max-heal") + 1])

    print(f"=== AAC pipeline — {pkg.name} (human over the loop) ===")

    if not skip_synth:
        r = run("atlas_to_concept.py", pkg)
        if r.returncode != 0:
            print(r.stdout.strip()[-600:])
            print("\nSTOP: synthesis refused. The exact atlas.json fixes are listed above (human/agent edit).")
            raise SystemExit(1)
        print("  [1/6] concept map synthesized, lint clean")

    r = run("concept_to_process.py", pkg)
    print("  [2/6] process cards " + ("generated" if r.returncode == 0 else f"FAILED\n{r.stdout}{r.stderr}"))
    if r.returncode != 0:
        raise SystemExit(1)

    run("validate_agent_package.py", pkg)
    print("  [3/6] validated (readiness ladder written)")

    r = run("qa_agent_package.py", pkg)
    print("  [4/6] QA: " + (r.stdout.splitlines()[0] if r.stdout else "no output"))

    # Heal always runs at least once: repair_proposals.json IS the human queue artifact.
    for i in range(1, max_heal + 1):
        run("heal_agent_package.py", pkg)
        run("validate_agent_package.py", pkg)
        r = run("qa_agent_package.py", pkg)
        print(f"  [5/6] heal pass {i}: " + (r.stdout.splitlines()[0] if r.stdout else ""))
        report = load(pkg / "exports" / "qa_report.json", {})
        if not report or report["summary"].get("auto_fixable", 0) == 0:
            break

    if "--improve" in sys.argv:
        wf = load(pkg / "process" / "workflow.aac.json", {})
        for n in wf.get("nodes", []):
            nid = n["node_id"] if isinstance(n, dict) else n
            card = load(pkg / "process" / "nodes" / f"{nid}.aac.json", {})
            if card.get("runtime_mode") in {"C", "A"}:
                r = subprocess.run([sys.executable, str(SCRIPTS / "improve_node.py"), str(pkg), nid],
                                   capture_output=True, text=True)
                line = (r.stdout or r.stderr).strip().splitlines()
                print(f"  [improve] {line[0] if line else nid}")

    run("export_agent_map.py", pkg)
    print("  [6/6] combined map exported")

    qa = load(pkg / "exports" / "qa_report.json", {})
    readiness = load(pkg / "exports" / "readiness_report.json", {})
    proposals = load(pkg / "exports" / "repair_proposals.json", {}) or {}
    verdict = qa.get("verdict", "unknown")
    ladder = readiness.get("ladder", {})
    rings = [k.split("_")[0] for k, v in ladder.items() if isinstance(v, dict) and v.get("pass")]

    print(f"\nVERDICT: {verdict.upper()} | satisfaction {qa.get('satisfaction')} | "
          f"rings passed: {', '.join(rings) or 'none'}")
    queue = list((readiness.get("human_gates") or []))
    resolved = {s["id"] for s in qa.get("scenarios", []) if s.get("result") == "PASS"}
    queue += [f"repair proposal [{p['severity']}] {p['finding']}: {p['proposed_action']}"
              for p in (proposals.get("proposals") or []) if p.get("finding") not in resolved]
    if qa.get("summary", {}).get("pending_llm"):
        queue.append(f"{qa['summary']['pending_llm']} judgment QA check(s): run the blind LLM pass "
                     f"(dark-factory-qa) against .holdout/scenarios/agent-package.yml")
    print("\nHUMAN-OVER-THE-LOOP QUEUE (async; nothing inline is waiting):")
    if queue:
        for q in queue[:12]:
            print(f"  - {q}")
    else:
        print("  - empty")
    print(f"\nArtifacts: exports/qa_report.json, exports/readiness_report.json, "
          f"exports/repair_proposals.json, exports/agent_map.mmd")

    raise SystemExit(0 if verdict in {"allow", "revise"} else 1)


if __name__ == "__main__":
    main()

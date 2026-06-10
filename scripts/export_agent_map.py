#!/usr/bin/env python3
"""
Export the FULL agent mapping: concept layer + AAC process layer + traceability, in one artifact.

Reads  <package>/<slug>_concept_map/knowledge/exports/latest.rivermap.json
       <package>/process/workflow.aac.json (+ node cards)
       <package>/atlas/atlas.json, atlas/crosswalk.json, exports/readiness_report.json
Writes <package>/exports/agent_map.mmd   (Mermaid: concept subgraph + process subgraph + serves links)
       <package>/exports/agent_map.json  (unified machine-readable mapping)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SINKS = {"happy_sink": "Happy", "refuse_sink": "Refuse", "hard_refuse_sink": "Hard refuse"}


def load(p: Path, default=None):
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default


def mid(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def esc(s: str) -> str:
    return (s or "").replace('"', "'").replace("\n", " ")[:90]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/export_agent_map.py <package_dir>")
    pkg = Path(sys.argv[1]).resolve()
    map_dirs = sorted(pkg.glob("*_concept_map"))
    rivermap = load(map_dirs[0] / "knowledge" / "exports" / "latest.rivermap.json", {}) if map_dirs else {}
    wf = load(pkg / "process" / "workflow.aac.json", {})
    atlas = load(pkg / "atlas" / "atlas.json", {})
    crosswalk = load(pkg / "atlas" / "crosswalk.json", {})
    readiness = load(pkg / "exports" / "readiness_report.json", {})

    if not rivermap or not wf:
        raise SystemExit("Need concept export + workflow card first (run the pipeline in order).")

    node_ids = [n["node_id"] if isinstance(n, dict) else n for n in wf.get("nodes", [])]
    cards = {nid: load(pkg / "process" / "nodes" / f"{nid}.aac.json", {}) for nid in node_ids}

    lines = ["flowchart LR"]
    lines.append(f'  %% SPINE: {esc(atlas.get("spine", ""))}')
    ring = (readiness.get("ladder", {}) or {})
    passed = [k for k, v in ring.items() if isinstance(v, dict) and v.get("pass")]
    lines.append(f'  %% readiness: {", ".join(passed) if passed else "R0 blocked — see readiness_report.json"}')

    lines.append('  subgraph CONCEPT["Concept map — why it makes sense"]')
    lines.append("    direction LR")
    for n in rivermap.get("nodes", []):
        lines.append(f'    c_{mid(n["id"])}["{esc(n["label"])}"]')
    trunk_nodes = set()
    for e in rivermap.get("edges", []):
        arrow = "==>" if e.get("is_trunk") else "-->"
        lines.append(f'    c_{mid(e["from"])} {arrow}|{esc(e["type"])}| c_{mid(e["to"])}')
        if e.get("is_trunk"):
            trunk_nodes.add(f'c_{mid(e["from"])}')
            trunk_nodes.add(f'c_{mid(e["to"])}')
    lines.append("  end")

    lines.append('  subgraph PROCESS["AAC process graph — how it runs"]')
    lines.append("    direction LR")
    for nid in node_ids:
        card = cards.get(nid) or {}
        mode = card.get("runtime_mode", "?")
        deliverable = esc(card.get("deliverable") or card.get("purpose") or "")[:60]
        lines.append(f'    p_{mid(nid)}["{esc(nid)}<br/>[{mode}] {deliverable}"]')
    for sink, label in SINKS.items():
        lines.append(f'    p_{mid(sink)}(("{label}"))')
    for e in wf.get("edges", []):
        cond = esc(e.get("condition", ""))[:50]
        lines.append(f'    p_{mid(e["from"])} -->|{cond}| p_{mid(e["to"])}')
    lines.append("  end")

    used_sinks = {e.get("to") for e in wf.get("edges", [])} | {e.get("from") for e in wf.get("edges", [])}
    for nid in node_ids:
        cref = (cards.get(nid) or {}).get("concept_ref")
        if cref and not str(cref).startswith("TODO"):
            lines.append(f'  c_{mid(cref)} -. serves .-> p_{mid(nid)}')

    if trunk_nodes:
        lines.append("  classDef trunk stroke-width:3px;")
        lines.append("  class " + ",".join(sorted(trunk_nodes)) + " trunk;")
    lines.append("  classDef sink stroke-dasharray: 5 5;")
    lines.append("  class " + ",".join(f"p_{mid(s)}" for s in SINKS if s in used_sinks or True) + " sink;")

    out_mmd = pkg / "exports" / "agent_map.mmd"
    out_mmd.parent.mkdir(parents=True, exist_ok=True)
    out_mmd.write_text("\n".join(lines) + "\n", encoding="utf-8")

    unified = {
        "package": pkg.name,
        "spine": atlas.get("spine"),
        "atlas": {
            "id": atlas.get("atlas_id"),
            "node_count": len(atlas.get("nodes", [])),
            "confidence_counts": {
                c: sum(1 for n in atlas.get("nodes", []) if n.get("confidence") == c)
                for c in ("Hunch", "Reasoned", "Evidenced")
            },
            "validation_backlog": atlas.get("validation_backlog", []),
        },
        "concept_map": rivermap,
        "process": {"workflow": wf, "node_cards": cards},
        "traceability": crosswalk,
        "readiness": readiness,
    }
    out_json = pkg / "exports" / "agent_map.json"
    out_json.write_text(json.dumps(unified, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("Wrote:", out_mmd)
    print("Wrote:", out_json)


if __name__ == "__main__":
    main()

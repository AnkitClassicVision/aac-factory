#!/usr/bin/env python3
"""
Scaffold an AAC Agent Package: ATLAS layer + Concept Flow Map + AAC Process Graph.

Creates:
  concepts/<slug>/                      (from concepts/_template_agent_package)
  concepts/<slug>/<slug>_concept_map/   (from concepts/_template_concept_map)

Usage:
  python scripts/new_agent_package.py "Agent Name" [optional-slug]
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_TEMPLATE = ROOT / "concepts" / "_template_agent_package"
CONCEPT_MAP_TEMPLATE = ROOT / "concepts" / "_template_concept_map"


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "agent"


def personalize(path: Path, topic_name: str, slug: str) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace("{{TOPIC_NAME}}", topic_name).replace("{{SLUG}}", slug)
    path.write_text(text, encoding="utf-8")


def initialize_concept_db(db_path: Path, *, topic_name: str, slug: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "UPDATE map SET name = ?, description = ? WHERE id = ?",
            (
                f"{slug}_concept_map",
                f"Concept flow map (sense-making) for AAC agent: {topic_name}. "
                f"Synthesized from atlas/atlas.json.",
                "default",
            ),
        )
        conn.execute(
            "UPDATE node SET label = ?, short_def = ? WHERE id = ? AND map_id = ?",
            (
                f"{topic_name} — Concept Flow",
                f"Sense-making root for the {topic_name} agent workflow.",
                "node_root",
                "default",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python scripts/new_agent_package.py "Agent Name" [optional-slug]')

    topic_name = sys.argv[1]
    slug = sys.argv[2] if len(sys.argv) >= 3 else slugify(topic_name)

    dest = ROOT / "concepts" / slug
    if dest.exists():
        raise SystemExit(f"Destination already exists: {dest}")
    if not AGENT_TEMPLATE.exists():
        raise SystemExit(f"Missing template: {AGENT_TEMPLATE}")
    if not CONCEPT_MAP_TEMPLATE.exists():
        raise SystemExit(f"Missing template: {CONCEPT_MAP_TEMPLATE}")

    concept_dest = dest / f"{slug}_concept_map"

    shutil.copytree(AGENT_TEMPLATE, dest, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(CONCEPT_MAP_TEMPLATE, concept_dest, ignore=shutil.ignore_patterns("__pycache__"))

    for rel in ("CLAUDE.md", "README.md", "CONTROL_STATE.json", "atlas/atlas.json"):
        personalize(dest / rel, topic_name, slug)

    (dest / "CONCEPT.txt").write_text(topic_name + "\n", encoding="utf-8")
    (concept_dest / "CONCEPT.txt").write_text(topic_name + "\n", encoding="utf-8")

    concept_readme = concept_dest / "README.md"
    if concept_readme.exists():
        concept_readme.write_text(
            concept_readme.read_text(encoding="utf-8").replace(
                "Concept Package (Self-Contained)",
                f"Concept Package — {topic_name} (Concept Flow Map, AAC agent layer)",
            ),
            encoding="utf-8",
        )

    (dest / "process" / "nodes").mkdir(parents=True, exist_ok=True)
    (dest / "process" / "prompts").mkdir(parents=True, exist_ok=True)
    (dest / "process" / "evals").mkdir(parents=True, exist_ok=True)

    initialize_concept_db(concept_dest / "knowledge" / "knowledge.db", topic_name=topic_name, slug=slug)

    print("Created AAC agent package:", dest)
    print("Created concept map package:", concept_dest)
    print("Next: fill atlas/atlas.json (spine, clusters, nodes), then run scripts/atlas_to_concept.py", dest)


if __name__ == "__main__":
    main()

# AAC Agent Package — {{TOPIC_NAME}}

Vague idea in → production-mapped agent workflow out. Three aligned layers:

| Layer | Folder | Question it answers |
|---|---|---|
| ATLAS map | `atlas/` | What do we believe, with what evidence? |
| Concept flow map | `{{SLUG}}_concept_map/` | Why does this agent make sense? |
| AAC process graph + cards | `process/` | How does it run, with what gates, telemetry, and proof? |

Pipeline (see `CLAUDE.md`): interview → `atlas/atlas.json` → `atlas_to_concept.py` →
lint/export → `concept_to_process.py` → fill cards → `validate_agent_package.py` →
`export_agent_map.py` → human gates (golden grades, lane promotion).

Created from `_template_agent_package`. Scaffold: `python scripts/new_agent_package.py "Name"` (run from `knowledge_repo/`).

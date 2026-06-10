# Concept Package (Self-Contained)

This folder is a self-contained "concept package" that includes:
- its own SQLite DB (`knowledge/knowledge.db`)
- migrations + rules
- patches (SQL change sets)
- exports (JSON + Mermaid) for LLM consumption
- an agent playbook + CLAUDE controller

## Commands
```bash
# Apply a patch
python scripts/apply_sql.py knowledge/patches/<your_patch>.sql

# Lint (must return empty sets)
python scripts/lint_db.py

# Export
python scripts/export_map.py default
```

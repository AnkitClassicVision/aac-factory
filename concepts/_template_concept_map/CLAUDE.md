# CLAUDE.md — Concept Flow Map Builder (Sense-Making)

## Objective
Build a **conceptual flow map** (Justin Sung / iCanStudy style) in `knowledge/knowledge.db` and export JSON + Mermaid.

This is **not** a procedure manual. Convert procedures into concepts and relationships that explain:
- what the system *is*
- how it *makes sense* (logic trunk)
- how details expand outward (layers)

## Hard Rules
- Trunk = “how this makes sense” logic flow (Level 0).
- Trunk edges do **not** count toward the “groups of 3” constraint.
- Non-trunk outgoing edges: **max 3** per node (DB enforces).
- Structural edges: use `rel_part_of` to form a clean, fractal tree (each node has max 1 `part_of` parent).
- No-single-child rule: every node must have 0 children OR >=2 children (lint must be clean).
- No duplicate concepts (node labels unique per map).
- Every edge must include: relationship type + **why** + **how**.
- Avoid vague edges: `rel_related` is disallowed by lint; pick a specific relationship type.
- Max depth: 4–5 (design goal; use depth_hint if helpful).

## Files
- DB: `knowledge/knowledge.db`
- Migrations: `knowledge/migrations/`
- Patches: `knowledge/patches/`
- Lint: `knowledge/migrations/004_lint_queries.sql`
- Agent guidance: `prompts/agent_knowledge_playbook.md`
- Export: `scripts/export_map.py`
- Apply SQL: `scripts/apply_sql.py`
- Lint runner: `scripts/lint_db.py`

## Build Process (must follow)
1) Vocabulary dump: list key terms, principles, risks, metrics, constraints (concepts only; no steps).
2) Grouping: cluster into small groups (2–3) and name the “umbrella” concepts.
3) Trunk: select 5–9 Level-0 concepts and connect them as a clean logic chain (trunk edges).
4) Expand outward: for each trunk node, add up to 3 supporting concepts; expand deeper only if it improves understanding.
5) Crosslinks: allow only when they add obvious sense-making value; avoid hairballs by inserting bridge concepts.
6) Write a SQL patch under `knowledge/patches/`, apply, lint, fix until clean, export.

## Change Requests (No Drift / No Controller Bloat)
When the user asks for an update:
1) Confirm it’s a **change request** and restate acceptance criteria.
2) Update concepts/relationships via a new SQL patch in `knowledge/patches/`.
3) Keep the trunk coherent and the structure chunked (≤3 non-trunk children; no single-child).
4) Lint until clean, then export.
5) Verify the exported JSON/Mermaid matches the updated intent (portable context, avoids drift).

## DB write conventions
- map_id: "default"
- ids:
  - node: `node_<snake_case_label>_<short_hash>`
  - edge: `edge_<from>__<to>_<short_hash>`
- rel_type_id must exist in `rel_type` (see `knowledge/migrations/003_seed_rel_types.sql`).
- edge metadata requirements:
  - `why`: why this relationship matters
  - `how`: the mechanism/structure of the relationship

## Commands
```bash
python scripts/apply_sql.py knowledge/patches/<your_patch>.sql
python scripts/lint_db.py
python scripts/export_map.py default
```

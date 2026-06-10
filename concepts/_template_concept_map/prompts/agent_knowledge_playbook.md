# Agent Knowledge Playbook — Concept Flow Map (Sense-Making)

## Objective
Maintain a **conceptual flow map** in `knowledge/knowledge.db` so it stays:
- readable as a **logic trunk** (Level 0: “how this makes sense”)
- cleanly expanded as a **fractal tree** via `part_of` (Levels 1–4/5)
- free of “spider webs” (crosslinks are rare and justified)
- structurally constrained (groups of 2–3; max 3 non-trunk children per node)
- self-explanatory (every edge includes **why** + **how**)

## Required Workflow
1) Vocabulary dump: list key terms, principles, constraints, risks, metrics (concepts only; no “steps”).
2) Group: cluster into small groups (2–3) and name the umbrella concepts.
3) Trunk: choose 5–9 Level-0 concepts and connect them as the logic chain:
   - `rel_precedes` with `is_trunk=1`
4) Expand outward:
   - Use `rel_part_of` for the main structure (“this belongs under that”).
   - Keep ≤3 non-trunk outgoing edges per node (DB enforces).
   - Avoid single-child chains (lint enforces).
5) Crosslinks: only when they add obvious insight:
   - Use specific relations (enables/requires/causes/etc.).
   - `rel_related` is disallowed by lint.
6) Lint until clean, then export.

## Change Requests (Drift-Control Loop)
If the user asks to **change/update** the map, treat it as a change request:
1) Restate acceptance criteria (what must be true when done).
2) Update the map via a SQL patch under `knowledge/patches/` (avoid growing controller files).
3) Preserve the trunk and keep the structure fractal (2–3 children; no single-child).
4) Lint until clean, then export.
5) Verify the exports match the intended change (portable context).

## Canonical Queries
Root:
```sql
SELECT root_node_id, max_depth, max_children FROM map WHERE id = ?;
```

Children:
```sql
SELECT e.to_node_id, rt.name AS rel, e.is_trunk, e.branch_order
FROM edge e
JOIN rel_type rt ON rt.id = e.rel_type_id
WHERE e.from_node_id = ?
ORDER BY e.is_trunk DESC, e.branch_order ASC;
```

Lint (must be empty):
```sql
SELECT from_node_id, COUNT(*) AS child_count
FROM edge
GROUP BY from_node_id
HAVING child_count = 1;
```

## Commands (no sqlite3 CLI required)
```bash
python scripts/apply_sql.py knowledge/patches/<your_patch>.sql
python scripts/lint_db.py
python scripts/export_map.py default
```

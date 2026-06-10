# ATLAS Layer — Territory Map for the Agent Idea

`atlas.json` is the evidence-state map captured during the AAC interview (Flow Interviewer S0–S3).
It is the source the concept map is synthesized from, and the provenance anchor for every AAC card field.

## Rules
- **SPINE is one falsifiable claim**: who it is for + what it does + what counts as the win. Refuse mush.
- `spine_path` is the ordered sense-making chain (3–9 node ids). It becomes the concept-map trunk.
- `root_anchors` (0–2 node ids) are always-visible anchors under the topic root
  (convention: the work object and the win condition).
- Every node carries: `confidence` (Hunch | Reasoned | Evidenced), `provenance`
  (user | inferred-source | inferred-model | inferred-analogy | measured), `status` (open | building | proven),
  and a `proof_slot` (what would prove it). **Empty proof_slot means Hunch.**
- Edge types are fixed: serves | prereq | part-of | causes | feeds | differentiated-by | shaky | tension.
  `shaky` and `tension` edges never become concept-map edges; they become flags + validation backlog.
- Clusters group nodes; each cluster names a `trunk_node` from `spine_path` and lists `members`.
  A node belongs to ONE cluster (single structural parent downstream).
- `process_map` explicitly maps AAC process node ids → ATLAS node ids (preferred over fuzzy matching).
- Do not polish Hunches. Data updates confidence, can kill nodes, and may re-root the map.

## Lifecycle
1. Interview fills nodes/edges with provenance `user`.
2. Prefill (brain) adds `inferred-source` nodes; data probes upgrade fields to `measured`.
3. `atlas_to_concept.py` synthesizes the concept map and writes `crosswalk.json`.
4. Build progress and evidence re-score `status`/`confidence`. A stale ATLAS causes drift.

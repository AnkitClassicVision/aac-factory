# CLAUDE.md — {{TOPIC_NAME}} (AAC Agent Package — Master Controller)

This package turns a **vague agent idea into a production-mapped AAC workflow** through three aligned layers:

1) **ATLAS Map (Territory / Evidence State)** — `atlas/atlas.json`
   What we believe, with confidence + provenance + proof slots. Built during the AAC interview (Flow Interviewer S0–S3).
2) **Concept Flow Map (Sense-Making / Logic Trunk)** — `{{SLUG}}_concept_map/`
   WHY the agent makes sense. Synthesized FROM the ATLAS map (trunk = spine, branches = clusters).
3) **AAC Process Graph + Cards (Execution)** — `process/`
   HOW it runs: directed graph (nodes, edges, three sinks) with one AAC card per node carrying
   deliverable, artifact, owner, runtime mode, gates, telemetry, hard-refuse, kill switch.

AAC version context: AAC 2.5 pipeline (S0 Resolve … S9 Improve). This package implements the S4 stage family:
S4a ATLAS territory → S4b Concept synthesis → S4c Process graph + cards. Canonical AAC governance lives in the
[agent-automation-creator](https://github.com/AnkitClassicVision/agent-automation-creator) repo.

## Where To Go For What
- ATLAS rules + skeleton: `atlas/README.md`, `atlas/atlas.json`
- Concept map build rules: `{{SLUG}}_concept_map/CLAUDE.md`
- Process card rules: `process/README.md`
- Control state + flags: `CONTROL_STATE.json`
- Traceability: `atlas/crosswalk.json` (ATLAS node ↔ concept node ↔ process node)
- Readiness: `exports/readiness_report.json`

## Non-Negotiable Order
1) **ATLAS first.** Lock the SPINE (one falsifiable claim: who it is for + what it does + what counts as the win).
   Interview answers land in `atlas/atlas.json` with confidence/provenance/proof-slot per node. No mush spines.
2) **Concept Flow Map second.** Run the synthesizer, then enrich:
   `python ../../scripts/atlas_to_concept.py .` then lint + export inside `{{SLUG}}_concept_map/`.
3) **Process graph + AAC cards third.** `python ../../scripts/concept_to_process.py .`
   then fill every `TODO:` field from grill answers or live data probes (provenance: confirmed / measured — never silently assumed).
4) **Validate + export.** `python ../../scripts/validate_agent_package.py .` then `python ../../scripts/export_agent_map.py .`

## Self-Correcting Reinforcement (Deviation Protocol — three layers)
If you hit any of: unclear objective, conflicting/missing steps, errors, messy structure, uncertainty, or a change request:

0) Restate the change as acceptance criteria (what must be true after the update).
1) **Re-anchor to the SPINE** in `atlas/atlas.json`. If the belief changed, update the ATLAS node
   (confidence/provenance/status/proof-slot) first. New evidence can kill nodes — let it.
2) **Re-synthesize or patch the Concept Flow Map** so the trunk still tells the sense-making story
   (every edge keeps why/how; lint clean).
3) **Update the process cards** to match the concept map (do not let cards drift from the why).
4) Re-run validate + export. Update `CONTROL_STATE.json` (statuses + flags).

Drift check (THROUGHLINE): Aim — does every card serve the SPINE? Ground — are claims tied to
Evidenced ATLAS nodes or live probes? Thread — do crosswalk links still connect all three layers?

## Definition Of Done (crystallized, go-live-mappable)
- ATLAS: spine locked; every node tagged confidence/provenance/status/proof-slot; validation backlog ranked.
- Concept map: lint PASS + exports exist.
- Process: workflow card valid; every node has a complete card (no `TODO:` in required fields);
  graph reaches happy / refuse / hard-refuse sinks; every C node has model + prompt_ref + eval_ref + token budget.
- Crosswalk: every process node traces to a concept node and an ATLAS node; every trunk concept is covered.
- Golden set: harvested from real records and **graded by a human** (grade ≠ pending).
- `exports/readiness_report.json`: R0/R1 pass; R2+ blockers listed with owners. Lane promotion stays a human gate.
- No unresolved flags in `CONTROL_STATE.json`.

## Hard Boundaries
- This package designs and maps. It does NOT authorize sends, CRM/EHR writes, deploys, cron, or lane promotion.
- No PHI, secrets, raw CRM/contact IDs, or raw message bodies in any file here. Use redacted refs.
- Golden examples are harvested and graded, never authored from memory.

## Automation stance — human OVER the loop
- One command runs everything: `python ../../scripts/run_pipeline.py .`
  (synthesize → cards → validate → dark-factory QA → self-heal loop → re-validate → export → human queue)
- The system self-heals derivable defects from QA feedback (stale exports, missing stubs,
  telemetry/supervision/policy blocks). It NEVER auto-fixes meaning, runtime assignments,
  golden grades, or leak findings — those queue in `exports/repair_proposals.json`.
- Inline human gates exist only for the highest-risk classes: golden grading (async queue),
  residue signing, R3/R4 lane promotion, and any irreversible or external action.
  Everything else is supervised through run cards, sampling, and escalation triggers.
- QA style: dark-factory holdout. Criteria live in `.holdout/` (gitignored; the builder reads
  findings only via `exports/qa_findings.md`). Judgment criteria get a blind LLM pass via the
  dark-factory-qa skill against `.holdout/scenarios/agent-package.yml`.
- Runtime telemetry is a contract: every node card requires run-card metrics + artifact path;
  compiled agents emit run cards via `scripts/runcard.py` (escalations land in the review queue).

## Commands (run from this folder)
- Full pipeline (preferred): `python ../../scripts/run_pipeline.py .`
- Scaffold was created by: `python ../../scripts/new_agent_package.py "{{TOPIC_NAME}}"`
- Synthesize concept map: `python ../../scripts/atlas_to_concept.py .`
- Concept lint + export: `cd {{SLUG}}_concept_map && python scripts/lint_db.py && python scripts/export_map.py default`
- Generate process cards: `python ../../scripts/concept_to_process.py .`
- Validate readiness: `python ../../scripts/validate_agent_package.py .`
- QA only: `python ../../scripts/qa_agent_package.py .` | Heal only: `python ../../scripts/heal_agent_package.py .`
- Combined map export: `python ../../scripts/export_agent_map.py .`

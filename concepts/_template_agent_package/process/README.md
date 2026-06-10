# Process Layer — AAC Directed Graph + Cards

This folder holds the executable mapping of the agent workflow per AAC 2.5.

## Files
- `source_packet.aac.json` (optional input): interview packet / draft workflow card from Flow Interviewer.
- `workflow.aac.json` (generated, then human-confirmed): the directed graph — nodes, conditional edges,
  director, three sinks (happy / refuse / hard_refuse), owners, lanes, lane_promotion, observability, provenance.
- `nodes/<node_id>.aac.json`: one AAC node card per node. Required: runtime_mode (D/C/A/H), owner,
  input/output contracts, disciplines (bounded/grounded/gated/observed/governed), gates
  (input/output/cross_check/action), hard_refuse, telemetry, kill_switch — plus traceability
  (`concept_ref`, `atlas_ref`) and delivery (`deliverable`, `artifact`).
- `prompts/<node_id>.md`: prompt source for C nodes (`prompt_ref`), versioned via `prompt_version`.
- `evals/<slug>.golden.json`: golden set — harvested real records, human-graded (right | wrong | edit | pending).
- `run-cards/`: run-time proof artifacts land here when the agent runs (one folder per node).

## Rules
- The graph is the process. Every node must appear in `edges` and reach a sink.
- AI may select parameters inside a bounded action set; it may never invent the action class.
- `runtime_mode` (design-time performer), `max_lane` (design-time ceiling), and run-time proof
  (run cards) stay separate fields. Never overload one "mode" field.
- Every required field is filled or carries `TODO:` + provenance `assumed`. The validator counts TODOs
  and assumed fields as blockers — paper gates pass on fiction, data probes do not.
- No PHI, secrets, or raw CRM/contact IDs. Redacted refs only.

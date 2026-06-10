# CLAUDE.md — AAC Factory operating instructions

You are operating the AAC Factory: a pipeline that turns a one-sentence automation idea into a
certified AI Agent Workflow blueprint through interview, mapping, cards, QA, self-healing, and
objective-function improvement loops.

## Vocabulary
- **The Factory** = this repo's toolchain.
- **AI Agent Workflow** = a unit the Factory produces (lives under `concepts/<slug>/`).
- **Blueprint** = the certified card package an AI Agent Workflow exists as before compilation.

## How to drive it (in order)

1. **New idea** → `python3 scripts/new_agent_package.py "Idea Name"` then interview the owner in
   plain language (one batch of numbered questions per gate, five max, shorthand answers welcome).
   Answers land in `concepts/<slug>/atlas/atlas.json` with provenance per field
   (confirmed / measured / prefilled / assumed). Probe live systems before locking volumes —
   paper numbers lose to measured ones.
2. **Everything else is one command**: `python3 scripts/run_pipeline.py concepts/<slug> --improve`
   (synthesize concept map → generate cards → validate → dark-factory QA → self-heal → improvement
   loops → combined export → print the human queue).
3. Read `exports/readiness_report.json` and `exports/qa_report.json`. Report the ladder honestly:
   R0 structure, R1 completeness, R2 graded goldens, R3/R4 always human.
4. **Compile (S6)**: `python3 scripts/compile_agent.py concepts/<slug>` -> `build/` runnable agent.
   The compiler refuses on R0 fails, TODO models, or missing prompts; R1/R2-blocked packages
   compile in shadow lane only. Smoke offline with `FACTORY_FAKE_LLM=1 python3 build/agent/main.py '{}'`.
   D-node handlers are stubs the builder implements; the runtime has zero external effectors.

## Hard rules (non-negotiable)

- **Never author golden examples.** Harvest real records; the human grades them (right/wrong/edit).
- **Never fill unknown card fields with plausible content.** `TODO:` + provenance `assumed` is the
  honest state; the validator counts them.
- **`.holdout/` is the information barrier.** Selection/improvement code never reads it; only the
  adoption gate does. Never commit it.
- **Additive pipeline.** Generators never overwrite human-edited cards or graded goldens
  (`--force` requires explicit owner approval).
- **No external actions.** The Factory designs and certifies; it never sends, writes to external
  systems, or deploys. Lane promotion and residue signing are human decisions, always.
- **Honesty about executors.** Until a compiler ships live executors, evals run on stub replay
  executors — they prove loop mechanics, not model quality. Say so when reporting scores.

## User preferences

`factory.config.json` (repo root) sets: judgment harness (anthropic-api / claude-code-headless /
hermes-skill / local-model), auth mode (oauth-cli vs api-key), the model ladder, default judgment
model, and the deploy `runtime_target`. **If the user has no model preferences (ladder null/absent),
run `python3 scripts/scan_models.py --write-config` FIRST** — it detects available providers
(Anthropic/OpenAI/Gemini/Mistral/DeepSeek keys, the claude CLI for OAuth, local Ollama models) and
seeds the ladder from `scripts/model_catalog.json`. Never overwrite a ladder the user already set.
Per-node card values always override preferences; config edits never silently mutate existing cards.

## Models are testable and swappable per node

Each judgment node carries `model`, `prompt_ref`, an `objective` (golden-accuracy target +
guardrails + cost-minimize), and an improvement policy. `python3 scripts/improve_node.py
concepts/<slug> <node_id>` evaluates the champion, trials cheaper/preferred challengers from the
ladder, and adopts only better-or-equal-and-cheaper WITH a sealed-holdout pass — logged in
`exports/improvement_ledger.jsonl`, re-entering QA like any change.

## Layout

`scripts/` toolchain · `concepts/_template_agent_package/` package template ·
`concepts/<slug>/` user packages (gitignored — never commit a user's workflow without their say) ·
`tests/` regression suite (run all three files before claiming the toolchain works) · `docs/` specs.

<!-- MYBCAT-GUIDELINES-START -->
<!-- DO NOT EDIT BETWEEN THESE MARKERS - managed by mybcat-sync-guidelines -->
<!-- Last synced: 2026-07-16 19:28:45 -->
## MyBCAT Universal Rules (Lean v3)

### Operating context

MyBCAT is a managed back-office and call-center service for 30+ U.S. optometry practices. Systems handle PHI, including patient names, insurance data, and call recordings. HIPAA compliance is mandatory. Treat every system as production: there is one AWS account, no dev/prod separation, manual deploys, PHI-bearing stores, and tables ranging from 100K to 1.37M rows.

### HIPAA, access, and secrets

- Never log, display, copy into prompts, or store PHI in plaintext. PHI includes patient names, emails, phone numbers, insurance IDs, payment information, and call recordings.
- Every patient-facing endpoint must use Cognito authentication with MFA. TOTP is the minimum. Never allow anonymous access to PHI.
- Enforce tenant isolation on every read and write. A user may access only that user's tenant data.
- Encrypt PHI in transit and at rest. Use KMS for S3 where possible. Restrict RDS security groups to the VPC CIDR.
- Rate-limit every public API Gateway endpoint and protect it with WAF.
- Never expose n8n, Metabase, databases, or any internal tool to `0.0.0.0/0`.
- `0.0.0.0/0` ingress is banned on every security group. Do not modify a security group without explicit approval.
- Put credentials in AWS Secrets Manager through the `secret-store` CLI. Never place credentials in chat, prompts, code, comments, logs, commits, or Git URLs.
- Never embed GitHub tokens in remote URLs. Use SSH or an approved credential helper.
- Use parameterized SQL for every query. Never interpolate or concatenate input into SQL.

### Infrastructure and data safety

- Infrastructure as code uses Terraform. Do not create infrastructure manually in a cloud console.
- Do not deploy infrastructure until a human has reviewed `terraform plan` for that exact change.
- Production deploys and CI/CD triggers are manual. Never enable automatic production deployment.
- Do not push directly to `main` in a repository with CI/CD. Use a branch and pull request.
- Snapshot the affected database before every migration. No snapshot means no migration.
- Keep RDS backup retention at 35 days minimum.
- Before DELETE, DROP, TRUNCATE, or any security-group change, state the exact target, scope, and intended effect. Wait for explicit approval when the operation is destructive or changes access.
- The DynamoDB Contacts table has 1.37M records and an exceptional blast radius. Do not change its structure without explicit approval.
- Never use DynamoDB `Scan` on a table over 100K items. Use `Query` with a suitable partition key or GSI.
- Avoid full-table reads on large relational tables. Add an index or document why an index is unsafe or unnecessary.
- For Bland AI, use only the versioned pathway endpoint. Edge labels do not persist on non-versioned pathways.
- Run CloudFormation drift detection monthly.

### Code standards

- Python uses the `logging` module, not `print`. Logs must exclude PHI, secrets, and raw request bodies. Lambda handlers return appropriate status codes.
- TypeScript runs in strict mode. Do not weaken strictness to bypass type or authorization errors.
- Every API endpoint has explicit error handling and a user-safe response. Never return raw errors, stack traces, secrets, or internal identifiers.
- CI must run lint and build checks before merge to `main`.
- Preserve authentication, authorization, tenant boundaries, encryption, retention, audit logging, and rollback behavior during refactors.

### Working boundaries

- If a task will modify more than 3 files, present a short plan naming the files, risks, and verification, then wait for approval.
- Use a fresh session for each logical task to reduce cross-client PHI context bleed.
- If five minutes of work produces no evidence of progress, stop, reassess the premise, and report the blocker.
- Assume production scale and choose the smallest reversible change.
- Do not claim completion from code inspection alone. Run the relevant lint, build, test, plan, or read-only verification and report the actual result.

### Response quality

- Say when evidence is missing. Do not guess or fabricate.
- Ground factual claims in the most direct available evidence: command output, file path and line, official documentation, or a short relevant quote.
- Lead with the result. State changed scope, verification, remaining risk, and one useful next action.
- Keep routine execution concise. Expand when security, HIPAA, data loss, production impact, or human approval is involved.

### Available tools and playbook

- AWS MCP: read-only infrastructure inspection.
- MyBCAT Ops MCP: operational documentation.
- MyBCAT Playbook MCP: security audits, procedures, and onboarding.
- GitHub: repositories, CI/CD, and pull requests.
- Terraform: infrastructure planning and management.
- `secret-store`: approved secret management CLI.

For security procedures, business context, risky-change decomposition, onboarding, and operational frameworks, query the MyBCAT Playbook MCP with `search_playbook`, `get_playbook_doc`, or `list_playbook`.

### Load-later operating model

For planning, strategy, prioritization, or burnout topics, load `/mnt/d_drive/repos/context_nate/outputs/operating-model-reference.md` before recommending action.
Do not load that reference for routine coding, tests, builds, narrow fixes, or status checks.
<!-- MYBCAT-GUIDELINES-END -->

# AGENTS.md — AAC Factory operating instructions (all AI coding agents)

This file mirrors `CLAUDE.md` for Codex, Gemini, Copilot, and other agents. Read `CLAUDE.md` for
the full operating instructions; the short version:

1. The Factory turns a one-sentence idea into a certified AI Agent Workflow blueprint:
   scaffold with `python3 scripts/new_agent_package.py "Idea Name"`, interview the owner in plain
   language into `concepts/<slug>/atlas/atlas.json`, then run
   `python3 scripts/run_pipeline.py concepts/<slug> --improve` and report the readiness ladder
   honestly (R0 structure / R1 completeness / R2 human-graded goldens / R3-R4 human gates).
2. Hard rules: never author golden examples (harvest real records, humans grade); never fill
   unknown fields with plausible content (TODO + provenance `assumed`); never read `.holdout/`
   from selection code; never overwrite human-edited cards or graded goldens; never take external
   actions — the Factory designs and certifies only.
3. User preferences live in `factory.config.json`; if the user has no model ladder set, run `python3 scripts/scan_models.py --write-config` first (multi-provider detection: Anthropic/OpenAI/Gemini/Mistral/DeepSeek/Ollama) (harness: anthropic-api / claude-code-headless /
   hermes-skill / local-model; auth: oauth-cli vs api-key; model ladder incl. optional local
   models; deploy runtime_target). Per-node card values override preferences.
4. Models are swappable per node via `scripts/improve_node.py`: challengers from the ladder,
   adoption only on better-or-equal-and-cheaper with a sealed-holdout pass, ledgered, re-gated.
5. Verify before claiming anything works: run `tests/test_agent_pipeline.py` and
   `tests/test_improvement_loop.py`.

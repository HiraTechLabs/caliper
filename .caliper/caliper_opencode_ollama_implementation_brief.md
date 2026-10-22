# Caliper — OpenCode, Ollama, and Self-Production Implementation Brief

## Mission

Implement the next production-ready increment of **Caliper**, the private software factory, in the existing repository:

- Repository: `https://github.com/HiraTechLabs/caliper`
- Caliper, OpenCode, and Ollama are already installed on the same server.
- The goal is to let Caliper use the free models available through OpenCode (including MiniMax, GLM Flash, and DeepSeek when available) and local models served by Ollama, while preserving Caliper as the sole workflow orchestrator.
- Caliper should eventually be able to work on its own repository safely, using an isolated branch/worktree, tests, independent review, and human approval.

This is an implementation task, not a request for a proposal only. Inspect the current repository and implement the work in small, verifiable increments. Do not assume the repository state described below is still exact; verify the current code first.

## Non-negotiable working rules

1. **Inspect before editing.** Read `AGENTS.md`, `README.md`, `.caliper/DECISIONS.md`, `.caliper/GRAPH.md`, the actual graph file, `caliper/http_models.py`, `caliper/models_config.py`, `caliper/workflow.py`, `caliper/project_docs.py`, repository/remote modules, relevant settings/UI code, and tests.
2. Do not replace or rewrite the architecture wholesale. Extend existing interfaces and patterns.
3. Do not claim an integration works until it has been exercised with a real local test or a clearly documented smoke test. Unit tests with fakes are necessary but not sufficient for claims about real provider connectivity.
4. Never ask the user to paste API keys, passwords, private SSH keys, or other secrets into chat, prompts, logs, Git, YAML, or test fixtures.
5. Never store API keys in `models.yaml`. Preserve the existing secret-store mechanism and file permissions.
6. Do not automatically merge code, deploy to Dev, or deploy to Prod. Keep human approval gates.
7. Do not give an execution agent unrestricted access to the host, arbitrary directories, secrets, or production deployment capabilities.
8. Work in a dedicated branch named `feature/opencode-ollama-adapters` unless the repository already has an equivalent branch. Never work directly on the default branch.
9. After every verified implementation milestone, update the project’s **decision record and graph**. Do not update them merely to claim unfinished work is complete. Decisions are append-only; follow the existing format and rules.
10. Keep changes small enough to review. Run the relevant tests after each milestone and the full test suite before reporting completion.
11. Do not ask broad or unnecessary questions. If a detail is missing, inspect the code and documentation, choose the safest compatible default, and record the decision.
12. Do not install new software or replace the already-installed OpenCode/Ollama. First discover their installed versions and available interfaces.

## Current architecture to verify

The current repository has been described as having:

- `caliper/http_models.py`: OpenAI-compatible HTTP client for `/chat/completions`; planning, execution, and review adapters; project graph and recent decisions included in model context.
- `caliper/models_config.py`: role-specific provider configuration, secret lookup, and connectivity probes. The three roles may currently be required to use distinct model names.
- `caliper/workflow.py`: planning → execution → review loop → human merge approval → Dev → Prod gates. The current deploy adapter may be mock-only.
- `caliper/project_docs.py`: project manifest, graph, and append-only decisions stored in the project repository.
- `models.yaml`: role configuration; secrets must remain outside this file.
- Real Git worktree/merge and real SSH/Docker deployment may be deferred. Verify before making any assumptions.

These notes are orientation only. The repository’s actual code and its current tests are authoritative.

## Goals

### A. Provider integration design

Support two provider paths without confusing their protocols:

1. **Ollama**: use its local OpenAI-compatible endpoint where supported by the installed version. Make host/base URL and model configurable. Use the existing HTTP adapter if it truly fits; do not duplicate it unnecessarily.
2. **OpenCode**: inspect the installed OpenCode CLI and its official local server/API documentation or built-in help. Do **not** assume `opencode serve` exposes an OpenAI-compatible `/chat/completions` endpoint. Determine the safest supported integration:
   - Prefer a small, explicit OpenCode adapter using its supported CLI/API.
   - Keep the integration behind Caliper’s existing model interface.
   - Invoke commands without shell-string interpolation; use argument arrays and bounded timeouts.
   - Capture stdout/stderr safely, redact secrets, and return clear actionable errors.
   - Avoid brittle parsing of human-oriented CLI output if a structured output mode exists.
   - Ensure the chosen interface can select the installed OpenCode provider/model IDs, including free models available to this account.
   - Never hardcode model IDs based on assumptions; discover or configure them.
3. Provider configuration must clearly identify provider/adapter type, base URL or executable as appropriate, model ID, timeouts, and secret reference where applicable. Preserve backwards compatibility with existing `models.yaml` if practical. If a schema migration is required, document it and provide safe defaults.
4. Do not weaken the existing role-separation rule without evidence. If current validation compares model names alone, evaluate whether the correct identity is `(provider, model)` and document the reasoning. Planning, execution, and review should remain independently configurable. Prefer a reviewer model different from the execution model and keep reviewer inputs free of hidden execution reasoning.

### B. Safe execution against a project checkout

Inspect whether the execution adapter currently only asks a model to return a unified diff. If so, implement or document a safe next step toward **real repository-aware execution**:

- Work only in a designated project clone and an isolated branch/worktree.
- Verify the target repository, base branch, clean state, and branch naming before edits.
- Never edit the default branch directly.
- Validate generated patches before applying them; reject path traversal, absolute paths, unexpected binary changes, and modifications outside the project root.
- Run configured tests and lint checks with bounded timeouts.
- Capture a reviewable diff and test results for the reviewer.
- On failure, preserve diagnostics and do not advance the workflow.
- Do not merge or deploy automatically.
- If a full safe implementation cannot fit in this increment, do not fake it. Add the minimum robust abstraction and tests, document the limitation, and record the next decision/task.

### C. Caliper working on Caliper

Make self-production possible only through the same controlled workflow as any other project:

1. Caliper repository configured as an ordinary project.
2. Create a `feature/<name>` branch or isolated worktree.
3. Load project manifest, graph, principles, and decisions into the appropriate model context.
4. Generate a plan.
5. Execute changes in the isolated checkout.
6. Run tests and validate the diff.
7. Have a separate review role inspect the plan, diff, test results, and applicable principles.
8. Allow bounded review iterations, with a clear maximum and visible failure state.
9. Require explicit human approval before merge.
10. Keep Dev and Prod as separate gates; never treat merge approval as permission for production deployment.

Do not build a second orchestration engine inside OpenCode. Caliper owns workflow state, approvals, audit trail, and policy enforcement. OpenCode and Ollama are model/runtime backends only.

## Implementation sequence

### Milestone 1 — Baseline and architecture

- Inspect the repo, branch, current working tree, test commands, installed OpenCode version/help, and Ollama version/models/endpoints.
- Run the existing test suite before editing and record the result.
- Identify the exact adapter contracts and config schema.
- Update the graph/decision records only if a verified architecture decision is made.

### Milestone 2 — Ollama support

- Configure an installed Ollama model through the existing HTTP adapter if compatible.
- Add tests for URL normalization, model selection, timeouts, errors, and secret-free logging.
- Provide a local smoke-test command that checks the configured model without exposing secrets.
- Do not assume a model is installed; detect/report missing model IDs clearly.

### Milestone 3 — OpenCode support

- Confirm the installed OpenCode interfaces using local help/version and supported documentation.
- Implement a dedicated adapter if needed; do not route OpenCode through the generic HTTP adapter unless the endpoint is proven compatible.
- Add unit tests for success, timeout, nonzero exit, malformed/empty output, missing executable, and safe argument handling.
- Add a clear health/probe command or status result.
- Document how to select a free model that is actually available to this installation/account. Do not promise free access or hardcode outdated free model names.

### Milestone 4 — Safe project execution

- Inspect current patch execution, Git operations, review flow, and approval gates.
- Implement safe isolated worktree/branch handling and patch validation if not already present.
- Keep the change scoped. If this milestone must be split, complete provider support first and explicitly leave execution hardening as a tracked follow-up rather than pretending it is complete.

### Milestone 5 — Self-hosted verification

- Run unit/integration tests.
- Run provider smoke tests only where the local runtime and model are available; record skipped tests accurately.
- Verify the reviewer is independent from execution where configuration permits.
- Verify no approval bypass, automatic merge, or production deployment was introduced.
- Test a small, harmless change to Caliper itself on a feature branch if the workflow is genuinely safe enough. Do not make a self-modifying test against the default branch.

## Required tests

Add or update tests covering at least:

- Existing configuration remains valid or has a documented migration.
- Provider type selection and `(provider, model)` identity validation.
- Ollama compatible endpoint and error cases.
- OpenCode command construction without shell injection.
- OpenCode timeout, missing binary, nonzero exit, empty/malformed response.
- Secret redaction in logs/errors.
- Execution restricted to the configured project root and isolated branch/worktree.
- Invalid patch/path traversal rejection.
- Test failure blocks progression.
- Review failure and iteration limit behavior.
- Human merge approval remains mandatory.
- Dev and Prod gates remain separate.
- Decision and graph updates reflect only verified behavior.

## Documentation and project memory

After each milestone is verified:

- Append a decision to the existing append-only decision log using the repo’s exact format.
- Update the actual project graph to reflect the implemented components and relationships.
- Update relevant README/configuration docs and example `models.yaml` without secrets.
- Keep the decision log and graph consistent with the code. Mark planned or incomplete functionality as such.
- Record exact test commands and outcomes, including skips and environment limitations.

## Acceptance criteria

The work is complete only when:

- Ollama can be configured through a verified supported path, with tests and clear setup instructions.
- OpenCode can be invoked through a verified supported interface, selecting a model configured/available in the installed OpenCode setup.
- Existing providers/workflows remain functional or migration impact is clearly documented.
- No credentials are committed or logged.
- Execution changes remain confined to an isolated project branch/worktree.
- Tests and review results are passed into the workflow.
- Human merge approval and separate deployment gates remain enforced.
- The graph and append-only decision log match what is actually implemented.
- Final report lists changed files, design decisions, test commands/results, provider smoke-test results, known limitations, and the branch/commit created. Never report an unrun test as passed.

## Git and reporting rules

- Use `feature/<feature-name>` for new features.
- Keep the work on `feature/opencode-ollama-adapters` unless an equivalent feature branch already exists.
- Commit only coherent, tested milestones with descriptive messages.
- Do not push, merge, deploy, or modify remote settings unless explicitly authorized by the user and supported by the available tools.
- At the end, provide a concise Arabic summary covering:
  1. What was actually implemented.
  2. Which tests ran and their exact results.
  3. Whether Ollama and OpenCode were tested against the real installed services.
  4. What remains incomplete.
  5. The branch and commit hash.
  6. Which decision and graph records were updated.

**Priority:** Correctness and safety over speed. Never claim completion merely because code was written.

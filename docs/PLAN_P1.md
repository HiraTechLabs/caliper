# PLAN P1 — Real environments, login-based model access, containerized Caliper

Audience: the AI coding agent working in this repo.
Read `AGENTS.md` first. Its rules (section 0 and 12.6 especially) apply to every phase. This plan overrides AGENTS.md only where section 8 lists amendments.

## 0. Problems to solve

1. **Environments are only a form.** Dev/Prod ask for host, SSH user, deploy path, compose file, health URL. That is not enough for real use:
   - No real credential handling (a user name alone cannot log in).
   - A compose file is assumed mandatory; many projects have a Dockerfile only, a registry image, a deploy script, or no Docker at all.
   - No place for the deployed app's own environment variables and secrets.
   - No rollback.
2. **Model access supports only an API key** (OpenAI-compatible) and the OpenCode CLI. Login-based access (CLI login / subscription, device-code login) must be supported without Caliper ever handling those credentials.
3. **Caliper runs as a bare host process.** It must be containerized and cloud-native.

Repo state (verified from an archive of the working tree, branch `feature/opencode-ollama-adapters`, 2026-10-22; branches: `main`, `feature/opencode-ollama-adapters`, `r2c/principles-environments`): the factory loop is not real yet. Findings that shape this plan:
- Deploy at runtime is `MockDeployAdapter` (default when no adapter is passed); "merge" only writes a marker file; there is no real git branch, merge, or push in the workflow.
- Execution never sees the project's code (its prompt has the request, plan, graph and decisions only) yet is asked for a unified diff. The OpenCode client runs with `cwd=None`, i.e. in Caliper's own directory, not in a request worktree.
- `patch_safety.py` (validate, safe apply, worktrees) exists but is imported only by tests.
- The review loop never re-runs Execution: a failed review is re-reviewed on the same diff. `request_changes` does not call Execution and the human note is unused.
- Review validation checks structure only: it does not require a verdict for every approved rule ID, so a review with one passing rule approves everything. The planner check is a prefix search ("SEC-", "ARC-"...), not a check against the approved IDs.
- `httpx` and `python-multipart` are needed at runtime but missing from `dependencies` in `pyproject.toml`.
- `ModelsSettings.save()` drops the `executable` field; API keys are plaintext files written then chmod-ed.
- Frontend is still Jinja2 + vanilla JS bundled with esbuild (`caliper/static/js/app.js` is the source, `caliper/static/app.js` the committed bundle). No React/Tailwind/shadcn yet. Line endings are LF (a CRLF impression came from a Windows-made archive); `.gitattributes` is missing.
- `main` is 1 commit behind `origin/main` and fails 1 test (private key fixture mode 0644 vs 0600) that the feature branch fixes. Feature branch: 169 tests pass. 8 environment tests fail wherever `ssh-keyscan` is missing.
- No CI exists (no `.github/`), although AGENTS.md section 8 requires three jobs. `node_modules/` is committed (about 1000 tracked files). Self-documentation is duplicated and diverged: `docs/{DECISIONS,GRAPH,graph}` vs `.caliper/{DECISIONS,GRAPH,graph}`; root `UI_SYSTEM.md` and `docs/UI_SYSTEM.md` contradict each other.
- Good and to be kept: repo-backed principles with states, per-environment keys with host-key pinning, project docs in `.caliper/`, safe deletion, SSE updates, the decision log (D-001..D-031), the OpenCode adapter design (no shell, no `--auto`, redaction).

## 1. Rules for this plan

- One phase at a time. After each phase: run everything, paste output, report "works / does not work / next", then STOP for human approval.
- No mocks in the running app. Tests may use fakes for unit tests, but each phase also needs an integration test against a real component (a real `sshd` container for environments, a real HTTP server for model endpoints).
- No passwords are ever stored. No secret appears in logs, prompts, the repo, API responses, or plaintext SQLite.
- Every new component, dependency, and CI job gets a `.caliper/DECISIONS.md` entry; `.caliper/graph.yaml` is updated in the same commit.
- UI: follow `docs/UI_SYSTEM.md`. If the frontend migration (React + shadcn) is not done yet, deliver API + tests for the UI parts, list the screens that the frontend port must add, and do NOT build new Jinja pages.
- If something in this plan conflicts with what the code actually does, stop and ask the human. Do not guess.

Phase order: 0 Verify, 0b Correctness fixes, 0c Make the loop real, 1 Container foundation + secret store, 2 Environments, 3 Model access, 4 CI + docs.
Reason: a factory that cannot yet run one real change end to end cannot produce itself, so 0b and 0c come first. Containers then define the topology (agent runner, volumes, networking) and the secret store is needed by both environments and model access.

---

## 2. Phase 0 — Verify (DONE)

Completed in `docs/AUDIT.md` (all section 0 findings confirmed; corrections folded into section 0 above). Remaining step before Phase 0b: land `feature/opencode-ollama-adapters` on `main` as recommended in the audit (fast-forward local `main` to `origin/main`, then `git merge --no-ff` the feature branch, push, run `pytest`; expect 169 passed). No Phase 0b fixes are bundled into that merge.

---

## 2b. Phase 0b — Correctness fixes (small, test first)

1. **Review loop.** When a review fails and loops remain, re-run Execution with the plan, the failed rule verdicts, and any human note, producing a NEW diff; then review again. `request_changes` must invoke Execution with the human note. Test: a failing review leads to a second Execution call whose prompt contains the failures.
2. **Review validation.** Verdicts must cover exactly the approved rule IDs: missing, unknown, or duplicate IDs make the review invalid (counts as a failed loop). The planner must cite at least one rule and every cited ID must be in the approved set; unknown IDs are rejected.
3. **Dependencies.** Add `httpx` and `python-multipart` to runtime `dependencies`. Add a test that installs the package into a clean virtualenv and starts the app, handles one form POST, and makes one HTTP model call against a local server.
4. **Settings.** Persist the `executable` field in `models.yaml` (round-trip test). Create secret files atomically with mode 0600 (`os.open` with mode), not write-then-chmod.
5. **Mock removal.** Delete `MockDeployAdapter` from `caliper/workflow.py`. `Workflow` requires a real deploy adapter; with none configured, approve/deploy returns a clear blocked state ("Deployment is not configured"). Test fakes live under `tests/`. Record a Decision resolving the contradiction between D-016/D-017 and D-022.
6. **Repository hygiene.** Add `.gitattributes` (`* text=auto eol=lf`). Remove `node_modules/` from Git (`git rm -r --cached node_modules`, add to `.gitignore`; keep `package.json` and the lockfile) and record a Decision on whether the esbuild bundle `caliper/static/app.js` stays committed or is built at install time. Mark tests that need `ssh`/`ssh-keyscan` with a skip that says why when the tool is missing.
7. **CI.** Add `.github/workflows/ci.yml` with exactly the three jobs from AGENTS.md section 8: `test` (clean virtualenv install of the package, then `pytest`), `lint` (style + type checks; choose and record the tools), `graph-check` (graph file consistent with the code and decisions updated). Decision entry for each tool.
8. **One home for self-documentation.** Recommended: `.caliper/` is authoritative for Caliper's own decisions, graph, and manifest (it is what Caliper reads into prompts); `docs/` keeps only human-facing documents (UI_SYSTEM, AUDIT, PLAN, README material). Merge the diverged copies (for example D-031 exists only in `docs/DECISIONS.md`, `docs/GRAPH.md` has 18 nodes vs 16), delete the duplicates, point `graph-check` at the surviving files, and amend AGENTS.md section 5 accordingly. Do this only after the human confirms the choice.
9. **UI system.** Keep a single `docs/UI_SYSTEM.md`: the v2 spec (React + shadcn/ui + Tailwind, motion). Remove the root copy and the contradictory Jinja/HTMX version. Add a Decision noting that the current Jinja UI is a transitional stack that the frontend port replaces. Do not build new Jinja pages.
10. **Graph accuracy.** Update `graph.yaml` descriptions that claim behavior the code lacks (review validation, deploy adapter) so they state the current truth; they change again when 0b/0c land.

Acceptance: full suite green; each fix has a test that failed before the fix.

---

## 2c. Phase 0c — Make the loop real

Goal: one real change goes request -> plan -> edit -> tests -> review -> approval -> merge, with no mock in the path. This is what allows Caliper to produce itself.

1. **Worktree per request.** Use `patch_safety.create_worktree` on the project's clone: branch `caliper/req-<id>` from the default branch. Remove it on completion or request deletion.
2. **Execution inside the worktree.**
   - `opencode` provider: run with `cwd` = the request worktree (the client already supports `cwd`). The prompt carries the plan, the approved principles and the instruction to edit files and run the project's tests. It does NOT ask for a diff. Never pass `--auto`.
   - `http` provider (no tools): the prompt includes the contents of the files the plan names (size-capped) and requires a unified diff, which is validated and applied with `patch_safety.validate_patch` / `apply_patch`.
   - In both cases Caliper computes the diff itself with `git diff` in the worktree. Reject an empty diff, a diff touching forbidden paths (patch_safety rules), or one over the size limit.
   - Residual risk until Phase 3 (agent isolation): the agent runs as the Caliper user. Document it in the README and DECISIONS.
3. **Test gate.** Run the project's test command in the worktree (`test_command` in `.caliper/manifest.yaml`; Caliper's own is `pytest`). A failure is fed back to Execution like a review failure, counts against the loop limit, and is shown on the request page.
4. **Review inputs.** Plan, diff, and test results only. Never the Execution transcript.
5. **Real merge on approval.** After the human approves, Caliper merges the branch into the default branch and pushes with the project's deploy key. No force push. A conflict or rejected push moves the request to `failed` with the reason. Record the merge commit SHA on the request.
6. **Statuses.** `merged` after a real merge. `deployed_dev` and `live` are reachable only through a real deploy adapter (Phase 2); until then the UI says "Deployment is not configured" and does not fake progress.

### Acceptance
- Integration test with a local bare git repo as the remote and a real local HTTP model server process: trivial change (add a function and its test) goes through every step and the commit appears in the remote's default branch. No mock adapter in the path.
- Failure-path tests: tests fail then pass on the second Execution; review fails on a missing rule ID; push rejected; empty diff.
- Manual milestone (human present): one tiny real request on the `caliper` project with real models; report the plan, diff, test output, review, and resulting commit.

---

## 3. Phase 1 — Container foundation and secret store

### Tasks
1. **Config.** All config via environment variables `CALIPER_*`. For every secret-like variable also support `<NAME>_FILE` (Docker/Kubernetes secrets). Every writable path is under `CALIPER_DATA_DIR` (default `/data`): `caliper.db`, `projects/`, `work/` (worktrees), `secrets/`. Nothing is written elsewhere (except tmpfs, below).
2. **Secret store.** Encrypted at rest with AES-256-GCM (`cryptography`), random nonce per secret. Master key from `CALIPER_MASTER_KEY_FILE` or `CALIPER_MASTER_KEY`. Caliper refuses to start without it and prints the exact command to create one (`python -m caliper init-secrets`, writes a 0600 key file). Document that a key stored next to the data is weaker than a Docker/K8s secret.
   - API: `put(scope, name, value)`, `get`, `delete`, `list(scope)` (names only). Scopes: `project/<id>`, `env/<project>/<env>`, `model/<role>`.
   - Migrate existing plaintext items under `CALIPER_SECRETS_DIR` (deploy keys, environment keys, model API keys) into the store, then securely remove the plaintext files. Provide a tested migration.
   - SSH needs key files: materialize a key to tmpfs (`/run/caliper`, mode 0600) only for the duration of the call, then delete.
3. **Health and lifecycle.** `/health/live` (process up) and `/health/ready` (DB reachable, data dir writable, secret store unlocked). JSON logs to stdout. On SIGTERM: stop accepting new runs, mark in-flight runs `interrupted` (resumable), exit cleanly.
4. **Dockerfile** (multi-stage; include a Node build stage only if the frontend exists): non-root user (uid 10001), read-only root filesystem with `/data` and `/tmp` as writable mounts, `HEALTHCHECK`, pinned base image and package versions, `git` and `openssh-client` installed.
5. **`compose.yaml`** at the repo root:
   - `caliper`: port published as `127.0.0.1:8080:8080` (SSH-tunnel access stays the default), volume `caliper-data`, secrets for the master key, memory limit.
   - Optional `ollama` service under a compose profile (`--profile ollama`), volume `ollama-models`, off by default.
   - NEVER mount the host Docker socket. Deploys to targets happen over SSH only.
6. **README**: `docker compose up -d`, first-run `init-secrets`, SSH tunnel command, how to upgrade, how to back up `/data`.

### Acceptance
- `docker compose up -d --build` then `curl http://127.0.0.1:8080/health/ready` returns 200.
- `docker compose restart caliper`: projects, principles state, and secrets still work.
- `docker inspect` shows non-root user, read-only rootfs, no socket mount.
- Existing plaintext secrets migrated; test proves no plaintext key remains on disk and no secret appears in logs or API responses.
- Full test suite passes inside the container.
- Report image size.

### Network note (must handle)
Inside a container `127.0.0.1` means the container itself. Anything that previously used `http://127.0.0.1:11434/v1` (Ollama) must use `http://ollama:11434/v1` (compose profile) or the host via `host.docker.internal` with `extra_hosts: ["host.docker.internal:host-gateway"]`. Update default hints in the models settings and map "connection refused" to this explanation.

---

## 4. Phase 2 — Environments

### 4.1 Model
An environment (Dev, Prod) is split into three parts:

| Part | Where it lives | Content |
|---|---|---|
| Connection | Caliper local store (DB) | host, port, user, auth method, credential reference, pinned host-key fingerprint |
| Deploy spec | Project repo `.caliper/deploy.yaml` (Git is source of truth) | strategy, parameters, health check, env var NAMES |
| Deployed app secrets | Caliper secret store, scope `env/<project>/<env>` | env var VALUES, injected at deploy time |

Example `.caliper/deploy.yaml` (no secrets):
```yaml
version: 1
environments:
  dev:
    strategy: compose            # compose | dockerfile | image | script
    compose_file: auto           # optional; auto-detect compose.yaml, compose.yml, docker-compose.yml, docker-compose.yaml
    health: { type: http, url: "http://localhost:8000/health", check_from: target, timeout_s: 60 }  # type: none | http | tcp | command
    env: [DATABASE_URL, SECRET_KEY]
  prod:
    strategy: image
    image: ghcr.io/org/app
    ports: ["80:8000"]
    health: { type: tcp, port: 80 }
```

### 4.2 Authentication methods (connection)
1. **Generated key (default).** Caliper generates an ed25519 key per environment (already exists). Public key plus exact `ssh-copy-id` command shown.
2. **Imported key.** The human pastes/uploads an existing private key (for example an AWS `.pem`). Optional passphrase. Stored only in the secret store.
3. **Password, bootstrap only.** The human enters the SSH password once in a form. Caliper uses it in memory to install its own generated public key into `authorized_keys`, verifies key-based login works, then discards the password. The password is never stored, logged, returned, or placed in an exception message (add a request-log filter and a test proving it). If key login cannot be verified, nothing is kept.
4. **Optional "Prepare host" (opt-in checkbox)** during bootstrap, requires sudo: create a dedicated deploy user, add it to the `docker` group, create the deploy path. Show exactly what will run before running it. Warn that `docker` group membership is root-equivalent.
5. Later (design for it, do not build): bastion/ProxyJump.

Host key pinning on first successful connection stays: show the fingerprint for confirmation; a later change fails loudly.

### 4.3 Deploy strategies
- `compose`: `docker compose up -d` with the detected or given compose file. The compose file is OPTIONAL in the UI and spec: if absent, auto-detect; if none found, the strategy cannot be `compose` and the UI says so.
- `dockerfile`: build an image from the repo's Dockerfile on the target, run one container (ports, env, restart policy `unless-stopped`).
- `image`: pull a registry image (`image`, optional private registry credential in the secret store) and run it.
- `script`: run a repo-provided script (default `.caliper/deploy.sh`) with documented environment variables (`CALIPER_ENV`, `CALIPER_COMMIT`, `CALIPER_RELEASE_DIR`). Escape hatch for non-Docker targets.
- Later (not now): `kubernetes`.

**Auto-detect** when a project is connected: scan for compose files, Dockerfile, k8s manifests, `.caliper/deploy.sh`; propose a strategy; the human confirms. UI shows only the fields relevant to the chosen strategy. Health check is optional (`none` allowed); `check_from: target` (via SSH on the target, default) or `caliper`.

### 4.4 Deploy execution
- `RemoteExecutor` interface over SSH (asyncssh or system `ssh` in BatchMode). Timeouts, streaming output to the request page over SSE, idempotent steps.
- Release layout on the target: `<deploy_path>/releases/<commit>` and a `current` link. Keep the last 3.
- On failed health check: automatically switch back to the previous release, mark the request Failed, show the logs. Never run `docker compose down -v` or delete volumes.
- Env var values are written to a 0600 file on the target (or passed with `-e` for `docker run`), never into the repo or logs.
- Prod deploy still requires human approval (unchanged).

### 4.5 Test connection (checklist result, each line pass/fail with an exact fix command)
TCP reachable, host key pinned, authentication OK, Docker present and version, compose plugin (only if strategy needs it), user can run Docker, deploy path exists and is writable, free disk space reported.

### Acceptance
- Integration tests run against a real `sshd` container (compose service used only by tests): key auth, imported key, password bootstrap (and proof the password is gone afterwards), host-key change failure, each strategy deploying a tiny test app, health failure triggers rollback.
- A project with only a Dockerfile (no compose file) deploys successfully.
- Env var values reach the deployed app and appear nowhere in logs or API responses.
- `.caliper/deploy.yaml` is read from the repo; editing it in the UI commits it.

---

## 5. Phase 3 — Model access beyond API keys

### 5.1 Provider types per role
1. `openai_http`: base URL, model, optional API key (existing). Covers Ollama and hosted gateways.
2. `agent_cli`: a CLI agent that keeps its OWN credentials. Fields: preset (`opencode` now; `custom` for others), `model` (`provider/model` form), and a command spec: run command and arguments template, model flag, `auth_status_cmd`, `login_cmd`.
   - Presets other than OpenCode ship only after their flags are verified against the CLI's own `--help` and documentation at implementation time. Do not guess flags.

### 5.2 Login-based access
- Caliper never reads, copies, or stores credentials that a CLI manages. It only runs `auth_status_cmd` and interprets the exit code/output as Logged in / Not logged in / Unknown, and shows the exact `login_cmd`.
- In containers the login command is shown as `docker compose exec -it agent-runner <login_cmd>`. Device-code flows print a URL and code; the human completes them in their own browser. A web terminal is out of scope.
- The CLI's credential store lives in a dedicated volume `agent-home` mounted only into `agent-runner`.
- Ollama cloud models: `ollama signin` is performed on the Ollama service. Test connection runs a minimal completion and maps authentication errors to that instruction.
- Note for the human, shown in the UI help: provider terms may restrict automated use of subscription logins. Use only CLIs and plans that allow non-interactive use.

### 5.3 Isolation (security requirement)
- New service `agent-runner` (own image): contains the agent CLIs and `git`, runs as non-root, NO Caliper secrets, NO deploy keys, NO Docker socket, NO master key.
- Shared volume `/data/work` (request worktrees) is mounted into both containers. Git operations (clone, commit, push) happen ONLY in the `caliper` container. The runner only edits files in the worktree.
- Runner exposes an internal HTTP API (internal network only, shared token via secret): start job (request id, worktree path, model, prompt), stream output (SSE), cancel, status. Enforced timeouts and output size limits.
- Caliper computes the diff after the job. Review receives the diff and the plan only, never the Execution transcript.
- Optional hardening to document: restrict the runner's network egress to the model endpoints it needs.

### 5.4 Settings UI (spec for the frontend port)
Each role card has a segmented control: `API key` | `CLI login`.
- API key: base URL, model, key, Test.
- CLI login: preset, model, status (Logged in / Not logged in / Unknown), login command with copy, `Re-check`, Test.
Distinctness check compares normalized (type, endpoint host, model id); saving is rejected when two roles are the same, and the message names both roles.

### 5.5 Usage tracking
Record tokens and cost per role per request when the provider reports them. When it does not (some CLIs), record "unknown". Never fabricate numbers.

### Acceptance
- Integration test: a real local HTTP model-endpoint stub process for `openai_http`; a real runner container executing a trivial CLI-shaped command for `agent_cli` job flow (start, stream, cancel, timeout).
- Test proving the runner container cannot read `/data/secrets`, the master key, or any deploy key.
- Auth status shows correct state before and after a login performed with the real OpenCode CLI (manual verification by the human is acceptable and must be requested explicitly).
- Execution end-to-end: plan -> runner edits worktree -> Caliper diff -> review, on a throwaway project.

---

## 6. Phase 4 — CI and documentation

1. GitHub Actions: keep `test`, `lint`, `graph-check`. Add a fourth job `image` (build the container image, push to `ghcr.io/hiratechlabs/caliper` tagged by commit SHA on `main`). Adding it requires a DECISIONS entry (AGENTS.md section 8 says exactly three jobs; this is a deliberate amendment).
2. Update README (container-first run instructions), `.caliper/DECISIONS.md`, `.caliper/GRAPH.md` and `.caliper/graph.yaml` (new nodes: secret store, agent-runner, remote executor, ollama service).
3. Apply the AGENTS.md amendments in section 8.

### Acceptance
CI green on a PR; image published; README instructions verified from a clean machine by the human.

---

## 7. Out of scope (do not build)

Kubernetes manifests or Helm, bastion/ProxyJump, multi-replica Caliper or a Postgres backend, Caliper self-deploying itself, a web terminal, Caliper-managed OAuth flows against providers, product-level (COS) features, UI work on Jinja pages.

State the single-instance limitation in the README: SQLite plus a persistent volume means one replica. Keep all state under `/data` so a later split is possible; record a Decision that Postgres is deferred.

## 8. AGENTS.md amendments to apply in Phase 4

- Section 6: Caliper runs as containers (`caliper`, `agent-runner`, optional `ollama`); "one process" becomes "one compose command".
- Section 7b: Access stays loopback + SSH tunnel but now maps to the published `127.0.0.1:8080`; Core <-> models adds the runner API; Core <-> Dev/Prod adds deploy strategies and rollback.
- Section 8: CI has four jobs including `image`.
- Section 12.2 / 12.5: replace with the environment and model-access designs above (compose file optional, credentials per 4.2, login-based models per 5.2).
- Section 12.8: "Settings > Models supports an agent-CLI adapter later" becomes implemented.

## 9. Report format after each phase

1. Commands run and their real output.
2. Acceptance checklist with pass/fail per item.
3. What works, what does not, what is next.
4. Decisions recorded and graph changes.
Then stop and wait for approval.

# GRAPH.md — Caliper component graph

`.caliper/graph.yaml` is the **source of truth** for nodes (components) and edges (relations).
The CI `graph-check` job fails if a node path or an edge references a missing component, if a
`caliper/` module has no node, or if the decision index and entries disagree.

## Snapshot (container foundation, encrypted secrets, real factory loop)

```
browser
  |
  v
app-core (FastAPI: dashboard, projects, setup, composer, request page, /api JSON, SSE,
          /health/live + /health/ready, run() with JSON logs and graceful SIGTERM)
  |  composition root
  +--> runtime-config (CALIPER_* env + _FILE, /data layout, master key, init-secrets)
  +--> secret-store (AES-256-GCM SQLite; scoped names only)
  |       ^                         ^                         ^
  |       |                         |                         |
  |   project-registry         models-config          legacy-migration
  |       |                         |                         |
  |       +--> secret-materialization (0600 tmpfs key files, secure unlink)
  +--> process-lifecycle (active runs, interrupted on shutdown/startup recovery)
  +--> logging-setup (JSON stdout, redaction)
  +--> workflow-engine (plan -> execute in worktree -> test -> review Git diff -> approve
                        -> merge/push -> dev/prod only with a real adapter)
          | prepares/tests/reviews/merges       | role calls
          v                                     v
   request-workspace (Git diff, tests, merge/push)   http-model-adapters / opencode-adapter
          | validates/applies        | clones/syncs/merges/pushes
          v                          v
   patch-safety                 git-repo-access ---uses deploy key of---> remote-access
          | writes worktrees
          v
       work-store (<data>/work/<project>/<request>)

project-docs (.caliper manifest/graph/decisions)   deploy-adapter (none configured; Dev/Prod blocked)
project-store (projects/<id>/: manifest, repo clone, req-<n>/ artifacts)
project-registry (manifest, encrypted deploy/env keys, principle records, connection tests)
request-store (SQLite run state + Git evidence + audit events) --publish--> event-bus --> app-core SSE
app-ui (Jinja2 screens + vanilla JS)
cli (`python -m caliper serve|init-secrets`)
tests/ — pytest over every node above, including real temporary Git remotes and a gated
         real-container acceptance test
```

## Nodes

| id | path | kind | what it is |
|---|---|---|---|
| `app-core` | `caliper/main.py` | application | Composition root + HTTP: dashboard `/`, projects `/projects`, add form, project page + setup checklist, composer, request page + run/approve/changes/prod/delete actions, `Settings`/models at startup, `/health` (liveness alias), `/health/live`, `/health/ready`, `run()` entrypoint, the JSON API under `/api` for repository access, principles, project docs, environments, project/request deletion, and `GET /api/events` (SSE). Startup validates config, unlocks the secret store, runs the one-way migration and marks in-flight runs interrupted; shutdown refuses new runs and records interruption. |
| `runtime-config` | `caliper/config.py` | infrastructure | `CALIPER_*` configuration with `<NAME>_FILE` for secrets; the `/data` layout (`caliper.db`, `projects/`, `work/`, `secrets/`, `models.yaml`) plus the ephemeral runtime dir; directory creation and writability checks; master-key decoding/loading and atomic 0600 `init-secrets` generation. Refuses to start without a valid 32-byte key. |
| `secret-store` | `caliper/secrets.py` | infrastructure | AES-256-GCM SQLite secret store with a fresh 96-bit nonce per write, scope+name authenticated data, scopes `project/<id>`, `env/<project>/{dev,prod}`, `model/{planning,execution,review}`. `put/get/delete/exists/list(scope)/delete_scope`; `list` returns names only and errors never include a value. |
| `secret-materialization` | `caliper/secret_files.py` | infrastructure | Writes one decrypted private key into the runtime tmpfs with mode 0600, yields it for exactly one operation, then zero-overwrites and unlinks it on success, failure or cancellation. Never writes key material under `/data`. |
| `legacy-migration` | `caliper/migrate.py` | infrastructure | One-way Phase 0c migration: copies `.caliper-state/state.sqlite3` after an integrity check (legacy copy kept), copies packaged model settings once, and moves plaintext model/deploy/env keys and known_hosts into the encrypted store/public cache only after a verified read-back. Conflicts, wrong keys and failed reads leave the plaintext in place; reruns are safe. |
| `process-lifecycle` | `caliper/lifecycle.py` | infrastructure | Tracks active request ids; stops accepting runs at shutdown, sets the workflow stop event, marks in-flight requests `interrupted` (resumable, not `failed`) and waits a bounded grace period. Startup recovery uses the same status. |
| `logging-setup` | `caliper/logging_setup.py` | infrastructure | One JSON stdout handler for Caliper and uvicorn (`ts`/`level`/`logger`/`message`) plus a redaction filter. Request bodies, model prompts, API keys and private keys are never logged. |
| `cli` | `caliper/cli.py` | application | `python -m caliper serve` (default) starts `caliper.main.run` with optional host/port; `python -m caliper init-secrets` generates a 0600 master key without importing the app or opening the store. |
| `workflow-engine` | `caliper/workflow.py` | domain | Synchronous lifecycle: submitted→planning→executing→reviewing→(a failed test or failed/invalid review re-runs Execution with the failed verdicts, human note and test output, ≤3 executions, then `failed` for human)→awaiting_approval→approve (verifies the reviewed commit SHA, diff hash and clean worktree, performs a real `git merge --no-ff` and push, then sets `merged`)→`deployed_dev`→approve_prod→`live` only when a real deploy adapter exists; otherwise Dev/Prod stay blocked with "Deployment is not configured". Preparation creates one worktree on `caliper/<request id>` and every attempt resets it to the base commit. The reviewer sees the authoritative Git diff plus the test output, never the model's diff text. Review verdicts must cover exactly the approved rule IDs; an invalid review counts as a failed loop. A stop event at a phase boundary records `interrupted`, not `failed`. Takes the rule IDs from the project's **approved** principles. |
| `request-workspace` | `caliper/workspace.py` | domain | The only place that touches real Git after planning: clones/syncs the project repository, creates one worktree per request under `CALIPER_WORK_ROOT` (default `<data>/work/<project>/<req>`) on `caliper/<req>`, resets it per attempt, applies model diffs only through `patch_safety`, stages and computes the authoritative `git diff --cached <base>`, validates it, commits with Caliper's identity, runs the project's `test_command` (900 s) read from `.caliper/manifest.yaml` at the base commit, verifies the reviewed commit/diff hash and clean status, performs `git merge --no-ff` plus a deploy-key push, aborts conflicts, resets on push rejection, builds the size-capped code context for HTTP Execution, and removes worktrees idempotently. |
| `http-model-adapters` | `caliper/http_models.py` | domain | Role adapter classes over any chat client with a `post()` method (provider-agnostic). Default client: OpenAI-compatible HTTP with bearer key and injectable transport — this is how a local Ollama is reached too. Planner output must cite approved rule IDs only; executor returns a fenced diff (a re-run prompt carries the failed rule verdicts and the human note); reviewer JSON `{rule_id: {status, reason}}` with one verdict per approved rule and no duplicate keys. Disabled by default. |
| `opencode-adapter` | `caliper/opencode_models.py` | domain | OpenCode CLI client: `opencode run --format json --model <provider/model>` as an argv array (never a shell), bounded timeout, token redaction in errors, plain failures (missing executable, nonzero exit, timeout, empty/malformed output). `--auto` is never passed; OpenCode keeps its own credentials. |
| `patch-safety` | `caliper/patch_safety.py` | domain | The only path that applies a model diff: static validation (absolute paths, traversal, backslashes, `.git` internals, binary, malformed/overlarge/empty rejected) and safe application via `git apply --check` then `git apply` (argv, bounded timeout) inside one worktree; per-request isolation on `caliper/<request id>`. Idempotent, symlink-safe removal. Wired into `request-workspace` (D-031, D-046, D-047). |
| `models-config` | `caliper/models_config.py` | domain | Loads/validates `models.yaml` from `CALIPER_MODELS_CONFIG` (default `<data>/models.yaml`): 3 roles, provider http&#124;opencode, base_url, model, timeout, `executable` for opencode roles; DISTINCT model ids (the model id is the identity, the provider is only a transport). API keys live only in the encrypted secret store under `model/<role>`; no plaintext key file is written. Builds HTTP role adapters backed by ChatClient or OpenCodeClient per role. Blank roles block workflow runs. |
| `smoke-cli` | `caliper/smoke.py` | application | `python -m caliper.smoke planning\|execution\|review [--timeout N]`: loads one configured role, sends one tiny prompt through the real provider and prints the reply without the saved API key; exits 1 with a plain reason when the role is unconfigured or the call fails. |
| `deploy-adapter` | `caliper/workflow.py` | domain | Deploy adapter interface on `Workflow` with **no runtime implementation yet**. Approval performs the real Git merge and push through `request-workspace` and sets `merged` only after the push succeeded; there is no marker file. With no adapter, Dev/Prod deploy calls raise `DeploymentNotConfigured` ("Deployment is not configured") and the request page shows that state — no `deployed_dev`/`live` is faked. Test doubles live under `tests/`. The SSH/docker adapter arrives in Phase 2 (D-036). |
| `principles` | `caliper/principles.py` | domain | Rule format (`XXX-NNN`), the three states (missing/draft/approved), the starter document (heading + one instruction line, never invented rules) and `PrinciplesService`: reads/writes `.caliper/principles/*.md` in the project repository, records the approval as a content hash + commit, and resets an approved file to Draft when it changes. |
| `project-docs` | `caliper/project_docs.py` | domain | Reads/writes `.caliper/manifest.yaml`, `.caliper/graph.yaml` and append-only `.caliper/decisions/*.md` in the project repository. Reads the local clone for page loads, syncs on demand, creates only missing docs, validates graph node/edge references, and supplies graph + decisions to model prompts. |
| `git-repo-access` | `caliper/repo.py` | infrastructure | One project repository through the git CLI and the project deploy key: clone, default branch detection, fetch/reset of that branch, read a file, write + commit + push one or many files to the default branch. |
| `remote-access` | `caliper/remote.py` | infrastructure | ed25519 key generation for a caller-provided path (the registry generates into the runtime tmpfs and stores the private key encrypted), repository read/write probe (`ls-remote` + `push --dry-run`), host key scan + SHA256 fingerprint, host key pinning per environment in `secrets/projects/<project>/known_hosts_<env>`, and the environment probe (`ssh true` + `docker compose version` + deploy path exists and writable) with plain-language failures and the exact fix command. |
| `project-registry` | `caliper/registry.py` | domain | Creates/loads projects: the local runtime `manifest.yaml` (repo URL + verification, optional `product:`, the state of the six principles, dev/prod environment definitions + host key fingerprint), the per-project deploy key and per-environment SSH keys **encrypted in the secret store** (`project/<id>`, `env/<project>/<env>`), public-key caches under `secrets/public/`, key contexts that materialize a private key only in the runtime tmpfs, and the repository/environment connection tests. The portable project manifest is `.caliper/manifest.yaml` in the repository. |
| `request-store` | `caliper/state.py` | data-store | SQLite run state at `CALIPER_DB_PATH` (default `<data>/caliper.db`): `requests` (id/kind/text/extra/status/loop_count/review_note plus Git evidence: branch, worktree, base/reviewed/merge commits, diff hash, failure reason), append-only `audit_events`; `ping()` for readiness, publishes committed changes to the event bus and supports single-request delete, cancel/restore, delete-in-one-transaction, and `mark_interrupted` (resumable `interrupted` status after a restart). Safely deletable; single-writer by design. |
| `event-bus` | `caliper/events.py` | infrastructure | In-process fan-out from request-store writes to per-browser SSE queues; bounded queues drop stale tokens for slow clients. |
| `project-deletion` | `caliper/deletion.py` | domain | Safe "remove from Caliper": cancel active requests, optional Dev/Prod `docker compose down` before any deletion, abort on failure, remove only resolved paths under `CALIPER_DATA_DIR/projects/<id>` and `CALIPER_DATA_DIR/work/<id>`, delete the project/env secret scopes and public cache, then delete DB rows + audit. Never touches the GitHub repository or `.caliper/` files. |
| `app-ui` | `caliper/templates` | presentation | Transitional Jinja2 screens with vanilla JS: dashboard, list, add-project, project page with setup checklist/environments/delete dialog and a Docs section (manifest, graph, decisions, sync/init/add decision), request page (stepper, review failed-first, plan, diff, approve gates, request delete confirmation), composer, empty/404/error states, light/dark toggle. Vanilla JS subscribes to SSE and updates request states without reload. This is not the target UI and does not conform to `docs/UI_SYSTEM.md` v2; the React + shadcn/ui port replaces it (D-044). No Phase 1 UI changes. |
| `project-store` | `projects/` | data-store | Runtime project data: the local clone of the repository and `req-<n>/` artifacts. Worktrees are separate under `work-store`. Env-overridable; not committed. |
| `work-store` | `work/` | data-store | Disposable request worktrees, one per project/request under `CALIPER_WORK_ROOT` (default `<data>/work`). Confined to this root; project deletion resolves and asserts the path before removal. Not committed and not a source of truth. |
| `tests` | `tests/` | test-suite | Tests across all nodes (unit + UI flows + engine + HTTP adapters via fake transport + OpenCode adapter via stub executable/JSON-lines runner + patch safety against real local git repositories + **the real factory loop against temporary bare Git remotes**: worktree commit, test gate, authoritative diff review, approval binding, real merge/push, unsafe diffs, conflict, push rejection, OpenCode worktree edits, an HTTP adapter round trip against a real local HTTP model endpoint, interruption recovery + settings incl. per-role providers + repository access + principles against a local Git repository + project docs Git round trip, decision authoring and prompt context + environments with a fake SSH runner + host key pinning + safe project/request deletion + SSE + Phase 1 config/master-key + AES-GCM store/tmpfs materialization + legacy migration + health/interrupted shutdown + static container/Compose invariants). The real-container acceptance test is gated by `CALIPER_CONTAINER_IMAGE`. Real ssh-keygen/ssh-keyscan tests skip with a reason when the tool is absent. Test doubles (role adapters, deploy adapter) live only here. |

## Key edges (full list lives in graph.yaml)

`app-core` loads `runtime-config`, unlocks the `secret-store`, runs `legacy-migration`, tracks
runs through `process-lifecycle`, configures `logging-setup`, uses `workflow-engine`, exposes the
JSON API for principles, project docs, environments and deletion, and streams `event-bus` updates
over SSE. `cli` reads `runtime-config` and starts `app-core`. The `secret-store` is written by
`project-registry` (deploy/env keys), `models-config` (API keys) and `legacy-migration`;
`secret-materialization` reads values from it and `project-registry` uses it for one operation.
The engine calls `http-model-adapters`, takes its rule IDs from `principles`, reads graph and
decisions through `project-docs`, prepares/tests/reviews/merges through `request-workspace`,
writes artifacts into `project-store`, and deploys through `deploy-adapter`. `request-workspace`
validates and applies model diffs through `patch-safety`, clones/syncs/merges/pushes through
`git-repo-access`, and writes request worktrees into `work-store`. `principles` and
`project-docs` read and write through `git-repo-access`, which uses the project deploy key of
`remote-access`. `project-registry` stores the approval records of `principles` and the local
runtime manifest, and uses `remote-access` for the keys and the connection tests.
`project-deletion` cancels through `request-store`, optionally stops environments through
`remote-access`, removes `project-store`/`work-store` data and encrypted scopes, and writes the
audit event. `app-ui` displays data from the registry, request store and project docs, and
subscribes to SSE. `tests` cover everything, including the real factory loop against temporary
Git remotes, the Phase 1 container/secret path, and the only test doubles (role and deploy
adapters).

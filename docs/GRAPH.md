# GRAPH.md — Caliper component graph

`docs/graph.yaml` is the **source of truth** for nodes (components) and edges (relations).
When CI is wired, a `graph-check` job will fail if `graph.yaml` references a missing component
or the code drifts from it.

## Snapshot (setup gate, project docs, model settings, safe deletion, SSE)

```
browser
  |
  v
app-core (FastAPI: dashboard, projects, setup, composer, request page, /api JSON, SSE, /health)
  |  loads at startup                         | delete-project
  v                                           v
models-config (models.yaml: 3 roles, provider http|opencode)   project-deletion (cancel -> optional stop -> remove -> audit)
  | builds                                            | stops through
  +---> http-model-adapters (also behind local Ollama)      v
  +---> opencode-adapter (opencode run --format json)     remote-access (ssh + docker compose)
  |
  v
workflow-engine (plan -> execute -> review -> approve -> dev -> prod)
  | role-calls                    | deploys-through
  v                               v
principles (rule format, states, approval at content hash + commit)
  | reads/writes in              |
  v                               v
project-docs (.caliper manifest/graph/decisions)   deploy-adapter (mock records)
  | reads/writes in
  v
git-repo-access (clone, default branch, commit + push)
  | uses the deploy key of
  v
remote-access (ed25519 keys, ssh probe, host key pinning + fingerprint)
  |
  v
project-store (projects/<id>/: manifest, repo clone, req-<n>/ artifacts)   <- written by
project-registry (manifest, deploy/env keys, principle records, connection tests)
request-store (SQLite run state + audit events) --publish--> event-bus --> app-core SSE
app-ui (Jinja2 screens + vanilla JS)
tests/ — pytest over every node above (154 passing)
```

## Nodes

| id | path | kind | what it is |
|---|---|---|---|
| `app-core` | `caliper/main.py` | application | Composition root + HTTP: dashboard `/`, projects `/projects`, add form, project page + setup checklist, composer (global + per project), request page + run/approve/changes/prod/delete actions, `Settings`/models at startup, `/health`, `run()` entrypoint, the JSON API under `/api` for repository access, principles, project docs, environments, project deletion and request deletion, and `GET /api/events` (SSE). |
| `workflow-engine` | `caliper/workflow.py` | domain | Synchronous lifecycle: submitted→planning→executing→reviewing→(changes loop ≤3→`failed` for human)→awaiting_approval→approve&merge→`deployed_dev`→approve_prod→`live`. Takes the rule IDs of the project from its **approved** principles; a project without approved rules cannot run. |
| `http-model-adapters` | `caliper/http_models.py` | domain | Role adapter classes over any chat client with a `post()` method (provider-agnostic). Default client: OpenAI-compatible HTTP with bearer key and injectable transport — this is how a local Ollama is reached too. Planner output must cite rule IDs; executor fenced diff; reviewer JSON `{rule_id: {status, reason}}`. Disabled by default. |
| `opencode-adapter` | `caliper/opencode_models.py` | domain | OpenCode CLI client: `opencode run --format json --model <provider/model>` as an argv array (never a shell), bounded timeout, token redaction in errors, plain failures (missing executable, nonzero exit, timeout, empty/malformed output). `--auto` is never passed; OpenCode keeps its own credentials. |
| `models-config` | `caliper/models_config.py` | domain | Loads/validates `models.yaml` (3 roles: provider http&#124;opencode, base_url, model, timeout; DISTINCT models — the model id is the identity, the provider is only a transport). Builds HTTP role adapters backed by ChatClient or OpenCodeClient per role. Blank roles block workflow runs. |
| `deploy-adapter` | `caliper/workflow.py` | domain | `MockDeployAdapter` writes deploy logs for dev/prod. Real SSH/docker compose adapter deferred until a real env exists. |
| `principles` | `caliper/principles.py` | domain | Rule format (`XXX-NNN`), the three states (missing/draft/approved), the starter document (heading + one instruction line, never invented rules) and `PrinciplesService`: reads/writes `.caliper/principles/*.md` in the project repository, records the approval as a content hash + commit, and resets an approved file to Draft when it changes. |
| `project-docs` | `caliper/project_docs.py` | domain | Reads/writes `.caliper/manifest.yaml`, `.caliper/graph.yaml` and append-only `.caliper/decisions/*.md` in the project repository. Reads the local clone for page loads, syncs on demand, creates only missing docs, validates graph node/edge references, and supplies graph + decisions to model prompts. |
| `git-repo-access` | `caliper/repo.py` | infrastructure | One project repository through the git CLI and the project deploy key: clone, default branch detection, fetch/reset of that branch, read a file, write + commit + push one or many files to the default branch. |
| `remote-access` | `caliper/remote.py` | infrastructure | ed25519 key generation under `CALIPER_SECRETS_DIR` (mode 600), repository read/write probe (`ls-remote` + `push --dry-run`), host key scan + SHA256 fingerprint, host key pinning per environment in a known_hosts file, and the environment probe (`ssh true` + `docker compose version` + deploy path exists and writable) with plain-language failures and the exact fix command. |
| `project-registry` | `caliper/registry.py` | domain | Creates/loads projects: the local runtime `manifest.yaml` (repo URL + verification, optional `product:`, the state of the six principles, dev/prod environment definitions + host key fingerprint), the per-project deploy key, the per-environment SSH keys and known_hosts files, and the repository/environment connection tests. The portable project manifest is `.caliper/manifest.yaml` in the repository. |
| `request-store` | `caliper/state.py` | data-store | SQLite run state: `requests` (id/kind/text/extra/status/loop_count/review_note) plus append-only `audit_events`; publishes committed changes to the event bus and supports single-request delete, cancel/restore, and delete-in-one-transaction. Safely deletable. |
| `event-bus` | `caliper/events.py` | infrastructure | In-process fan-out from request-store writes to per-browser SSE queues; bounded queues drop stale tokens for slow clients. |
| `project-deletion` | `caliper/deletion.py` | domain | Safe "remove from Caliper": cancel active requests, optional Dev/Prod `docker compose down` before any deletion, abort on failure, remove only resolved paths under `CALIPER_DATA_DIR/projects/<id>` and `CALIPER_SECRETS_DIR/projects/<id>`, then delete DB rows + audit. Never touches the GitHub repository or `.caliper/` files. |
| `app-ui` | `caliper/templates` | presentation | Screens per draft UI_SYSTEM: dashboard, list, add-project, project page with setup checklist/environments/delete dialog and a Docs section (manifest, graph, decisions, sync/init/add decision), request page (stepper, review failed-first, plan, diff, approve gates, request delete confirmation), composer, empty/404/error states, light/dark toggle. Vanilla JS subscribes to SSE and updates request states without reload. |
| `project-store` | `projects/` | data-store | Runtime project data incl. the local clone of the repository and `req-<n>/` artifacts. Env-overridable; not committed. |
| `tests` | `tests/` | test-suite | 154 tests across all nodes (unit + UI flows + engine + HTTP adapters via fake transport + OpenCode adapter via stub executable/JSON-lines runner + settings incl. per-role providers + repository access + principles against a local Git repository + project docs Git round trip, decision authoring and prompt context + environments with a fake SSH runner + host key pinning + safe project/request deletion + SSE). Mock role adapters live only here. |

## Key edges (full list lives in graph.yaml)

`app-core` uses `workflow-engine`, loads `models-config`, exposes the JSON API for
principles, project docs, environments and deletion, and streams `event-bus` updates over SSE.
The engine calls `http-model-adapters`, takes its rule IDs from `principles`, reads graph and
decisions through `project-docs`, writes artifacts into `project-store`, and deploys through
`deploy-adapter`. `principles` and `project-docs` read and write through `git-repo-access`,
which uses the project deploy key of `remote-access`. `project-registry` stores the approval
records of `principles` and the local runtime manifest, and uses `remote-access` for the keys
and the connection tests. `project-deletion` cancels through `request-store`, optionally stops
environments through `remote-access`, removes local data, and writes the audit event. `app-ui`
displays data from the registry, request store and project docs, and subscribes to SSE.
`tests` cover everything, including the only mock role adapters.

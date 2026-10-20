# GRAPH.md — Caliper component graph

`docs/graph.yaml` is the **source of truth** for nodes (components) and edges (relations).
When CI is wired, a `graph-check` job will fail if `graph.yaml` references a missing component
or the code drifts from it.

## Snapshot (R2c: principles in the repository, environments with pinned host keys)

```
browser
  |
  v
app-core (FastAPI: dashboard, projects, composer, request page, /api JSON, /status, /health)
  |  loads at startup
  v
models-config (models.yaml: 3 roles, DISTINCT models)
  | builds
  +---> http-model-adapters (OpenAI-compatible; optional bearer key)
  |
  v
workflow-engine (plan -> execute -> review -> approve -> dev -> prod)
  | role-calls                    | deploys-through
  v                               v
principles (rule format, states, approval at content hash + commit)
  | reads/writes in              |
  v                               v
git-repo-access (clone, default branch, commit + push)   deploy-adapter (mock records)
  | uses the deploy key of
  v
remote-access (ed25519 keys, ssh probe, host key pinning + fingerprint)
  |
  v
project-store (projects/<id>/: manifest, repo clone, req-<n>/ artifacts)   <- written by
project-registry (manifest, deploy/env keys, principle records, connection tests)
request-store (SQLite run state) --publish--> app-core /status polling
app-ui (Jinja2 screens)
tests/ — pytest over every node above (93 passing, 1 skipped on Windows)
```

## Nodes

| id | path | kind | what it is |
|---|---|---|---|
| `app-core` | `caliper/main.py` | application | Composition root + HTTP: dashboard `/`, projects `/projects`, add form, project page, composer (global + per project), request page + run/approve/changes/prod actions, `Settings`/models at startup, `/status` JSON, `/health`, `run()` entrypoint, and the JSON API under `/api` for repository access, principles and environments. |
| `workflow-engine` | `caliper/workflow.py` | domain | Synchronous lifecycle: submitted→planning→executing→reviewing→(changes loop ≤3→`failed` for human)→awaiting_approval→approve&merge→`deployed_dev`→approve_prod→`live`. Takes the rule IDs of the project from its **approved** principles; a project without approved rules cannot run. |
| `http-model-adapters` | `caliper/http_models.py` | domain | OpenAI-compatible HTTP roles with optional bearer key and injectable transport: planner output must cite rule IDs; executor fenced diff; reviewer JSON `{rule_id: {status, reason}}`. |
| `models-config` | `caliper/models_config.py` | domain | Loads/validates `models.yaml` (3 roles, base_url + model, DISTINCT models) and builds the role adapters. |
| `deploy-adapter` | `caliper/workflow.py` | domain | `MockDeployAdapter` writes deploy logs for dev/prod. Real SSH/docker compose adapter deferred until a real env exists. |
| `principles` | `caliper/principles.py` | domain | Rule format (`XXX-NNN`), the three states (missing/draft/approved), the starter document (heading + one instruction line, never invented rules) and `PrinciplesService`: reads/writes `.caliper/principles/*.md` in the project repository, records the approval as a content hash + commit, and resets an approved file to Draft when it changes. |
| `git-repo-access` | `caliper/repo.py` | infrastructure | One project repository through the git CLI and the project deploy key: clone, default branch detection, fetch/reset of that branch, read a file, write + commit + push to the default branch. |
| `remote-access` | `caliper/remote.py` | infrastructure | ed25519 key generation under `CALIPER_SECRETS_DIR` (mode 600), repository read/write probe (`ls-remote` + `push --dry-run`), host key scan + SHA256 fingerprint, host key pinning per environment in a known_hosts file, and the environment probe (`ssh true` + `docker compose version` + deploy path exists and writable) with plain-language failures and the exact fix command. |
| `project-registry` | `caliper/registry.py` | domain | Creates/loads projects: `manifest.yaml` (repo URL + verification, optional `product:`, the state of the six principles, dev/prod environment definitions + host key fingerprint), the per-project deploy key, the per-environment SSH keys and known_hosts files, and the repository/environment connection tests. |
| `request-store` | `caliper/state.py` | data-store | SQLite table `requests` (id/kind/text/extra/status/loop_count/review_note); safely deletable run state. |
| `app-ui` | `caliper/templates` | presentation | Screens per draft UI_SYSTEM: dashboard, list, add-project, project page, request page (stepper, review failed-first, plan, diff, approve gates), composer, empty/404/error states, light/dark toggle. |
| `project-store` | `projects/` | data-store | Runtime project data incl. the local clone of the repository and `req-<n>/` artifacts. Env-overridable; not committed. |
| `tests` | `tests/` | test-suite | 93 tests across all nodes (unit + UI flows + engine + HTTP adapters via fake transport + settings + repository access + principles against a local Git repository + environments with a fake SSH runner + host key pinning). |

## Key edges (full list lives in graph.yaml)

`app-core` uses `workflow-engine`, loads `models-config`, and exposes the JSON API for
principles and environments. The engine calls `http-model-adapters`, takes its rule IDs from
`principles`, writes artifacts into `project-store`, and deploys through `deploy-adapter`.
`principles` reads and writes the principle files through `git-repo-access`, which uses the
project deploy key of `remote-access`. `project-registry` stores the approval records of
`principles` and uses `remote-access` for the keys and the connection tests.
`app-ui` displays data from the registry and the request store. `request-store` holds the run
state. `tests` cover everything, including the only mock role adapters.

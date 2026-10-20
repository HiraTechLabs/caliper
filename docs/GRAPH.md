# GRAPH.md — Caliper component graph

`docs/graph.yaml` is the **source of truth** for nodes (components) and edges (relations).
When CI is wired, a `graph-check` job will fail if `graph.yaml` references a missing component
or the code drifts from it.

## Snapshot (after step 9: UI polish / end-to-end mock workflow)

```
browser
  |
  v
app-core (FastAPI: dashboard, projects, composer, request page, /status, /health)
  |  loads at startup
  v
models-config (models.yaml: 3 roles, DISTINCT models enforced)
  | builds
  +---> model-adapters (mock, default)
  +---> http-model-adapters (OpenAI-compatible; disabled by default)
  |
  v
workflow-engine (plan -> execute -> review -> approve -> dev -> prod)
  | role-calls                    | deploys-through
  v                               v
principles-templates          deploy-adapter (mock records; SSH/docker deferred)
  |
  v
project-store (projects/<id>/: manifest, principles, req-<n>/ artifacts)   <- written by
project-registry (validation + persistence)                                   workflow too
request-store (SQLite run state; deletable)   <- request UI reads this
app-ui (Jinja2 screens; vanilla-JS 3s poller keeps status pills live)
tests/ — pytest over every node above (58 passing)
```

## Nodes

| id | path | kind | what it is |
|---|---|---|---|
| `app-core` | `caliper/main.py` | application | Composition root + HTTP: dashboard `/`, projects `/projects`, add form, project page, composer (global + per project), request page + run/approve/changes/prod actions, `/status` JSON, `/health`, `run()` entrypoint. Env: `CALIPER_HOST/PORT/PROJECTS_ROOT/DB_PATH/MODELS_CONFIG/ARTIFACTS_ROOT`. |
| `workflow-engine` | `caliper/workflow.py` | domain | Synchronous lifecycle: submitted→planning→executing→reviewing→(changes loop ≤3→`failed` for human)→awaiting_approval→approve&merge→`deployed_dev`→approve_prod→`live`. Validates reviews (per-rule verdicts mandatory); artifact dirs; roles isolated. |
| `model-adapters` | `caliper/mock_models.py` | domain | Default mock roles with deterministic outputs citing every rule ID; optional deterministic failure mode for loop testing. |
| `http-model-adapters` | `caliper/http_models.py` | domain | OpenAI-compatible HTTP roles with injectable transport: planner output must cite rule IDs; executor fenced diff; reviewer JSON `{rule_id: {status, reason}}`. |
| `models-config` | `caliper/models_config.py` | domain | models.yaml load + hard validation (exactly 3 roles, distinct models) + adapter factory. Ships `adapter: mock` for all roles. |
| `deploy-adapter` | `caliper/workflow.py` | domain | `MockDeployAdapter` writes deploy logs for dev/prod. Real SSH/docker compose adapter deferred until a real env exists. |
| `principles-templates` | `caliper/principles.py` | domain | Six principles, numbered checkable rules (25 total: SEC/BUS/ARC/TEC/CLC/PRF). |
| `project-registry` | `caliper/registry.py` | domain | Creates/loads projects: manifest.yaml (repo mode/url, optional `product:`, dev/prod env definitions) + six principles files. |
| `request-store` | `caliper/state.py` | data-store | SQLite table `requests` (id/kind/text/extra/status/loop_count/review_note). |
| `app-ui` | `caliper/templates` (+ static) | presentation | Screens per draft UI_SYSTEM: dashboard (NEEDS YOU/IN PROGRESS/RECENT/All clear), list, add-project (3 fields), project page (envs + principles), request page (stepper, review failed-first, plan collapsed, diff, approve/request-changes gates, Prod confirm dialog), composer (one box + bug toggle + optional log), empty/404/error states, light/dark toggle. |
| `project-store` | `projects/` | data-store | Runtime project data incl. `req-<n>/` artifacts (plan.md, diff.patch, review.json, marker, deploy logs). Env-overridable; not committed. |
| `tests` | `tests/` | test-suite | 58 tests across all nodes (unit + UI flows + engine + adapters via fake transport). |

## Key edges (full list lives in graph.yaml)

`app-core` uses `workflow-engine` and loads `models-config` at startup; the engine calls the
role adapters (`model-adapters` by default, `http-model-adapters` when configured), reads rule
IDs from `principles-templates`, writes artifacts into `project-store`, and deploys through
`deploy-adapter`. `project-registry` writes `project-store` using `principles-templates`.
`app-ui` displays data from the registry and request store. `tests` cover everything.

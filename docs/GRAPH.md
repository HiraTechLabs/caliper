# GRAPH.md — Caliper component graph

`docs/graph.yaml` is the **source of truth** for nodes (components) and edges (relations).
This file is its human-readable snapshot. When CI is wired, a `graph-check` job will fail if
`graph.yaml` references a missing component or the code drifts from it.

## Current graph (after step 3: project registry)

```
+------------------+      +--------------------+      +--------------------------+
|     app-ui       |      |   project-registry |      |  principles-templates    |
| caliper/templates|----->| caliper/registry.py|----->| caliper/principles.py    |
| Jinja2 screens   | uses | create/list/get    | wri- | 6 principle definitions  |
+------------------+      |                    |  tes | numbered rule IDs        |
       ^                  +---------+----------+ +----+--------------------------+
       | renders                   | writes
+------+---------------------+     v
|         app-core           |   +-------------------+
| caliper/main.py            |   |  project-store    |
| FastAPI HTTP + run()       |   | projects/<id>/    |
| /health + UI routes        |   | manifest.yaml +   |
+---------------------^------+   | principles/*.md   |
                      | tests    | (runtime data)    |
               +------+---------+-------------------+
               |             tests                 |
               v                                   v
        tests/ (pytest)
```

## Nodes

| id | path | kind | what it is |
|---|---|---|---|
| `app-core` | `caliper/main.py` | application | FastAPI app factory: `GET /health`, project list `/`, add form `GET/POST /projects/new`, detail `/projects/{id}`, 404 page; hosts `run()` (`python -m caliper`, bind `127.0.0.1:8080` default, `CALIPER_HOST`/`CALIPER_PORT`/`CALIPER_PROJECTS_ROOT` overrides). |
| `project-registry` | `caliper/registry.py` | domain | `ProjectRegistry`: validation + id slug + persistence of `manifest.yaml` (repo mode/url, optional `product:`, dev/prod environment definitions) and principles; `ProjectError` for user errors. |
| `principles-templates` | `caliper/principles.py` | domain | The six principle definitions and the renderer producing numbered checkable rules (`SEC-001`…`PRF-004`). |
| `app-ui` | `caliper/templates` (+ `caliper/static`) | presentation | Server-rendered Jinja2 screens implementing draft `UI_SYSTEM.md`: base layout with sidebar + light/dark toggle, project list (EmptyState when empty), add-project form (3 fields), project detail (Environments, Principles), not-found page. |
| `project-store` | `projects/` | data-store | Runtime-only project data (`projects/<id>/`), path overridable via `CALIPER_PROJECTS_ROOT`; not committed to the Caliper repo. |
| `tests` | `tests/` | test-suite | pytest: smoke (`test_smoke.py`), registry behavior (`test_projects.py`), UI flows (`test_ui.py`). |

## Edges

| from | to | relation |
|---|---|---|
| `app-core` | `project-registry` | uses |
| `project-registry` | `principles-templates` | renders-and-writes (writes each principle file on project creation) |
| `project-registry` | `project-store` | writes |
| `app-ui` | `project-registry` | displays-data-from |
| `tests` | `app-core` | tests |
| `tests` | `project-registry` | tests |

## Not yet in the graph

Components planned by the work order but not built yet are **not listed** — nodes appear only
when they exist (with a `DECISIONS.md` entry in the same commit). Expected future nodes:
request intake, workflow engine (planning/execution/review adapters), model config, approval
gate, git layer, run-state store, HTMX partial updates.

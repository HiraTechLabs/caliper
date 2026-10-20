# GRAPH.md — Caliper component graph

`docs/graph.yaml` is the **source of truth** for nodes (components) and edges (relations).
This file is its human-readable snapshot. When CI is wired, a `graph-check` job will fail if
`graph.yaml` references a missing component or the code drifts from it.

## Current graph (step 1-2: scaffold)

```
                  +----------------+
                  |    app-core    |
                  | caliper/       |
                  | main.py        |
                  | FastAPI app,   |
                  | /health + run()|
                  +-------^--------+
                          |
                          | tests
                  +-------+--------+
                  |     tests      |
                  | tests/         |
                  | pytest suite   |
                  +----------------+
```

## Nodes

| id | path | kind | what it is |
|---|---|---|---|
| `app-core` | `caliper/main.py` | application | FastAPI app factory. Only endpoint: `GET /health`. Also hosts `run()` — the `python -m caliper` entrypoint (binds `127.0.0.1:8080` by default). |
| `tests` | `tests/` | test-suite | pytest suite. Currently `tests/test_smoke.py` — boots `app-core` and asserts `/health` returns 200 + `"ok"`. |

## Edges

| from | to | relation |
|---|---|---|
| `tests` | `app-core` | tests (the suite imports `create_app()` from `app-core` and exercises its HTTP surface) |

## Not yet in the graph

Components planned by the work order but not built yet are **not listed** — nodes appear only
when they exist (with a `DECISIONS.md` entry in the same commit). Expected future nodes:
project registry, request intake, workflow engine (planning/execution/review adapters),
model config, approval gate, git layer, state store, UI layer.

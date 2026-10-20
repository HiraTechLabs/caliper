# Caliper

Caliper is a private software factory: a single fixed workflow through which every change
(feature or bugfix) to a managed project must pass.

```
Request (feature | bugfix)
  -> Planning Model  -> plan
  -> Execution Model -> change on a branch (Dev env)
  -> Review Model    -> verdict against the project's Principles
  -> Human approval  -> merge -> Prod env
```

One Caliper hosts many **Projects**. It is not a build or supply-chain pipeline. AI roles are
disabled until human-approved adapters are enabled; human approval is mandatory for merge to
Dev and deploy to Prod.

## Run (one command)

Install once (from the repo root):

```
pip install -e ".[dev]"
```

Then run:

```
python -m caliper
```

The app serves `http://127.0.0.1:8080/health`. Host/port can be overridden with the
`CALIPER_HOST` and `CALIPER_PORT` environment variables.

## Run tests (one command)

```
pytest
```

## Remote (headless) access

Caliper binds to `127.0.0.1` by default and is meant to be used over an SSH tunnel from the
machine you work on. On a laptop/server that is only reachable over SSH:

```
ssh -L 8080:localhost:8080 <laptop>
```

then open `http://localhost:8080` in your browser on your local machine. No auth is exposed to
the network; all setup works without a desktop session.

## Repo layout

```
AGENTS.md    repo rules (read first)
docs/        self-documentation: docs/DECISIONS.md, docs/GRAPH.md + graph.yaml, docs/UI_SYSTEM.md (draft, awaiting human approval)
caliper/     application package: FastAPI app, project registry, Jinja2 templates + static CSS
projects/    runtime data (created on first project; not committed; CALIPER_PROJECTS_ROOT overrides)
tests/       tests (registry behavior, UI flows, smoke)
```

## CI

CI has exactly three jobs (to be wired as they become relevant):

- `test` — full suite.
- `lint` — style + type checks.
- `graph-check` — graph/log consistency.

## Status

Step 3 (project registry) done: create/list/view a project from the UI; each project gets a
`manifest.yaml` (repo, optional `product:`, dev/prod environments) and 6 principles files
with numbered checkable rule IDs. Nothing else exists yet — no request intake, no models, no
workflow. See `AGENTS.md` for the work order.

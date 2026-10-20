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

## Set up a project (principles and environments)

The principle files of a project live in **its Git repository** (`.caliper/principles/*.md`) and
are the source of truth. Caliper reads them from the default branch, commits what you write
back to that branch, and never invents a rule: a new principle file is a heading and one
instruction line. Each principle has a state: **Missing** (no file in the repository),
**Draft** (present or edited, not approved) and **Approved** (approved at a recorded content
hash and commit). Editing an approved file resets it to Draft. A rule is a list item whose text
starts with an ID like `SEC-001`; approval needs at least one rule and unique IDs.

The environment (Dev, Prod) has host, user, deploy path, compose file and health URL. Caliper
generates one ed25519 key per environment under `CALIPER_SECRETS_DIR` (mode 600) and never
stores a password; you run `ssh-copy-id` once with the command the API returns. The host key is
pinned on the first successful connection and shown as a fingerprint; a change fails loudly.
`Test connection` uses `BatchMode` and checks `ssh <host> true`, `docker compose version` and
that the deploy path exists and is writable; every failure comes back as a plain reason with
the exact fix command.

The repository itself uses a per-project deploy key (ed25519) and a read/write probe
(`git ls-remote` plus a push dry-run).

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

Step 9 done (end-to-end mock workflow): projects, request intake, planning/execution/review
pipeline with mock adapters (real OpenAI-compatible adapters wired but disabled by default),
review loop with 3-attempt limit, human approval gate -> merge -> Dev -> second gate -> Prod,
dashboard with live status polling. Real SSH/docker deploy and git branch/merge are the next
milestone items (see docs/DECISIONS.md, D-016/D-017).

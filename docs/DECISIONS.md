# DECISIONS.md — Caliper

Append-only. One entry per decision. Every entry is made **in the same commit** as the change.
Format: id, date, decision, reason, alternatives rejected.

## Index

| id | date | decision | commit |
|---|---|---|---|
| D-001 | 2026-10-20 | Scaffold bootstrap = one FastAPI app factory exposing only `/health` | 5d9a9b0 |
| D-002 | 2026-10-20 | One-command run = `python -m caliper` | 5d9a9b0 |
| D-003 | 2026-10-20 | Bind `127.0.0.1:8080` by default, overridable via env | 5d9a9b0 |
| D-004 | 2026-10-20 | Runtime deps limited to fixed stack only; state storage deferred | 5d9a9b0 |
| D-005 | 2026-10-20 | `UI_SYSTEM.md` lives in `docs/`, content untouched, status DRAFT | 5d9a9b0 |
| D-006 | 2026-10-20 | Self-documentation files created in step 2, retro-recording step-1 choices | (this) |
| D-007 | 2026-10-20 | CI wiring deferred until each job's inputs exist | (this) |
| D-008 | 2026-10-20 | Projects stored at `projects/<id>/` with manifest + 6 principles | (this) |
| D-009 | 2026-10-20 | One stack preset (`python-fastapi`) for now | (this) |
| D-010 | 2026-10-20 | Projects root configurable via `CALIPER_PROJECTS_ROOT` | (this) |
| D-011 | 2026-10-20 | Step-3 UI built from draft UI_SYSTEM.md | (this) |
| D-012 | 2026-10-20 | Requests are run state in SQLite (deletable), artifacts in project dirs | (this) |
| D-013 | 2026-10-20 | Workflow = synchronous pipeline behind three-role adapter interface | (this) |
| D-014 | 2026-10-20 | Review loop: fail -> executing, max 3 loops, then `failed` for the human | (this) |
| D-015 | 2026-10-20 | Startup fails fast on invalid/indistinct models.yaml | (this) |
| D-016 | 2026-10-20 | Real adapters = OpenAI-compatible HTTP, disabled by default (mock) | (this) |
| D-017 | 2026-10-20 | Deploy via adapter; mock records, SSH/docker adapter deferred | (this) |
| D-018 | 2026-10-20 | Dashboard home + live polling via tiny JS on `/status`, no HTMX yet | (this) |
| D-019 | 2026-10-20 | Principles live in the project repository; no generated rules | (this) |
| D-020 | 2026-10-20 | One SSH key per environment, host key pinned, plain-language failures | (this) |
| D-021 | 2026-10-20 | Project creation asks only name + repo URL; requests gated by a visible setup checklist | (this) |
| D-022 | 2026-10-20 | Model roles are blank by default; mock adapters live only under `tests/` | (this) |
| D-023 | 2026-10-20 | Safe project deletion + SSE request updates replace `/status` polling | (this) |
| D-024 | 2026-10-20 | Static asset URLs carry a version query; project settings/delete are a visible tab | (this) |
| D-025 | 2026-10-20 | A finished request can be deleted with its artifacts; in-flight requests are protected | (this) |
| D-026 | 2026-10-20 | Project decisions, graph and manifest live in `.caliper/` in the project repository | (this) |
| D-027 | 2026-10-21 | Decisions can be authored from the project Docs section | (this) |
| D-028 | 2026-10-21 | models.yaml gains a per-role `provider` (`http` default, `opencode`) plus `timeout` | (this) |
| D-029 | 2026-10-21 | OpenCode is integrated through `opencode run --format json` behind OpenCodeClient | (this) |
| D-030 | 2026-10-21 | Role distinctness stays on the model id; the provider is a transport only | (this) |
| D-031 | 2026-10-21 | Patch-safety primitives (validate + git apply + per-request worktree); runtime wiring deferred | (this) |
| D-032 | 2026-10-22 | A failed review re-runs Execution; request_changes calls Execution with the note | (this) |
| D-033 | 2026-10-22 | Reviews must cover exactly the approved rule IDs; plans must cite approved IDs | (this) |
| D-034 | 2026-10-22 | httpx and python-multipart are runtime dependencies; package data ships with the app | (this) |
| D-035 | 2026-10-22 | The OpenCode `executable` persists; API-key files are created at 0600 atomically | (this) |
| D-036 | 2026-10-22 | No deploy mock in the runtime: approve merges, deploy says "Deployment is not configured" | (this) |
| D-037 | 2026-10-22 | LF line endings, `node_modules/` untracked, the esbuild bundle stays committed | (this) |
| D-038 | 2026-10-22 | Tests that need real OpenSSH tools skip with an explicit reason | (this) |

## Entries

### D-001 — Scaffold bootstrap is a single FastAPI app factory with only `/health`
- **Decision:** `caliper/main.py` contains `create_app()` returning a FastAPI app whose only
  endpoint is `GET /health`. No UI, no templates, no other endpoints.
- **Reason:** Step 1 must prove the skeleton runs end-to-end with the least surface possible
  (vertical-slice-first, AGENTS.md rule 0.1). A health endpoint is the smallest testable
  HTTP surface and needs no human approval.
- **Alternatives rejected:** CLI-only scaffold (no verifiable HTTP surface); pre-building UI
  skeleton (violates rule: no screen without an approved UI_SYSTEM).

### D-002 — `python -m caliper` is the documented one-command run
- **Decision:** README documents `python -m caliper` (via `caliper/__main__.py`); a
  `caliper` console script entry point exists but is not the documented way.
- **Reason:** On this machine pip installs console scripts to the user Roaming directory
  which is not on PATH — unreliable on the headless servers this project targets.
  `python -m` works wherever `python` works.
- **Alternatives rejected:** `caliper` console script as primary command; wrapper .ps1.

### D-003 — Default bind `127.0.0.1:8080`, override via `CALIPER_HOST`/`CALIPER_PORT`
- **Decision:** The server binds to `127.0.0.1:8080` unless env vars `CALIPER_HOST` or
  `CALIPER_PORT` are set.
- **Reason:** Mandated by AGENTS.md 7b (headless access via SSH tunnel; nothing exposed to
  the network). Port 8080 matches the documented tunnel command `ssh -L 8080:localhost:8080`.
- **Alternatives rejected:** binding `0.0.0.0` (network exposure, not allowed); fixed port
  without override (breaks multi-instance/dev use).

### D-004 — Runtime deps = fixed stack only; state storage deferred
- **Decision:** Runtime dependencies are exactly `fastapi`, `uvicorn`, `jinja2`, `pyyaml`
  (AGENTS.md section 6). Dev deps: `pytest`, `httpx`. No SQLite code or schema yet.
- **Reason:** Step 1 has no state to store; AGENTS.md 2 forbids adding components without a
  reason. SQLite will be introduced when the first persistent state exists (project registry).
- **Alternatives rejected:** adding `pydantic-settings`, `alembic`, `sqlalchemy` now
  (nothing in step 1 uses them); external DB (forbidden by stack rule).

### D-005 — `UI_SYSTEM.md` kept as DRAFT, moved to `docs/`, content unchanged
- **Decision:** `UI_SYSTEM.md` moved from repo root to `docs/UI_SYSTEM.md` with zero content
  changes; it remains marked "DRAFT for human approval".
- **Reason:** AGENTS.md section 5 places self-documentation in `docs/`. Section 7 forbids
  building any page before the human approves this file, so it is stored but not acted on.
- **Alternatives rejected:** rewriting/polishing the draft (a human must approve the
  proposal first); leaving it at repo root (breaks self-documentation layout).

### D-006 — Self-documentation files created in step 2, retro-recording step-1 decisions
- **Decision:** `DECISIONS.md` and `GRAPH.md`/`graph.yaml` are created now (step 2) and
  record the scaffold's choices (D-001..D-005) that were made before these files existed.
- **Reason:** AGENTS.md work order puts scaffold (step 1) before self-doc (step 2), so the
  first entries record prior work. Entries are dated and commit-tagged to stay honest about
  what was decided when.
- **Alternatives rejected:** blocking step 1 until docs existed (not what the work order
  defines); omitting scaffold decisions (violates rule 0.5).

### D-007 — CI wiring deferred until each job's inputs exist
- **Decision:** CI is not wired yet. README describes the three jobs (`test`, `lint`,
  `graph-check`); they will be enabled as their inputs appear (e.g. `graph-check` needs
  `graph.yaml` + missing-component rule enforcement; `lint` needs its first code pass).
- **Reason:** `graph-check` defined in section 8 requires infrastructure that does not exist
  before step 2; adding a CI job now would ship a failing or useless job, violating rule 0.3
  (no stub jobs).
- **Alternatives rejected:** wiring `test` job only (arbitrary subset; all three should
  appear together with meaningful checks per AGENTS.md section 8).

### D-008 — Projects stored at `projects/<id>/` on the control plane
- **Decision:** Each project is a directory `projects/<id>/` containing `manifest.yaml` and
  `principles/{security,business,architecture,techstack,cleancode,performance}.md`, created by
  `ProjectRegistry` (caliper/registry.py). Manifest carries: id, name, created_at,
  stack_preset, repo `{mode, url}`, optional `product:`, and environment definitions
  (`dev`/`prod` with host + deploy command, initially blank).
- **Reason:** Matches AGENTS.md section 4's path convention. Git backend for repos arrives
  with the git layer (later steps); the optional `product:` field is accepted and stored now
  per section 11, with no product-level behavior attached.
- **Alternatives rejected:** SQLite for project definitions (Git/files are the source of
  truth; SQLite is for run state only, section 7b); creating actual git repos now (no git
  layer yet); putting product features behind the field (explicitly not allowed).

### D-009 — One stack preset (`python-fastapi`) for now
- **Decision:** The Add-project form ships with a `Stack preset` field containing a single
  option, "Python + FastAPI".
- **Reason:** AGENTS.md fixes the stack; presets may only be extended via a Decision entry.
  The field is kept so the form shape does not change when presets are added.
- **Alternatives rejected:** dropping the field (form would change later); inventing extra
  presets without decisions.

### D-010 — Configurable projects root via `CALIPER_PROJECTS_ROOT`
- **Decision:** Project storage root defaults to `<repo>/projects` and can be overridden
  with the `CALIPER_PROJECTS_ROOT` env var.
- **Reason:** Keeps runtime data out of the repo/copy of Caliper when deployed; lets
  integration tests and demos point at isolated directories, consistent with the existing
  `CALIPER_HOST`/`CALIPER_PORT` env pattern.
- **Alternatives rejected:** hardcoding one path (breaks deployment); storing inside the
  git repo as runtime data.

### D-011 — Step-3 UI built from the draft UI_SYSTEM.md (human-approved to proceed)
- **Decision:** Three screens (project list with EmptyState, Add-project form with exactly 3
  fields, project detail) built with Jinja2 server rendering, plain form POST (no HTMX yet),
  using only existing UI_SYSTEM components: Sidebar, Card, Button, Field, EmptyState,
  StatusPill (detail), and a light/dark theme toggle. No new component types were introduced.
- **Reason:** Step 3's definition of done requires the registry to be visible in the UI; the
  human approved proceeding to step 3, which includes these screens. `UI_SYSTEM.md` stays
  DRAFT pending final sign-off — no page content depends on parts not in the draft.
- **Alternatives rejected:** API-only step 3 (invisible to the human); HTMX partials now
  (no state to update live yet); new components (requires a Decision entry first).

### D-012 — Requests are run state in SQLite; artifacts live in project dirs
- **Decision:** `requests` table in SQLite (`CALIPER_DB_PATH`, default `.caliper-state/state.sqlite3`,
  gitignored) stores id/kind/text/extra/status/loop_count/review_note. Run artifacts
  (`plan.md`, `diff.patch`, `review.json`, merge/deploy markers) are written to
  `projects/<id>/req-<n>/`, next to the project's manifest and principles.
- **Reason:** AGENTS.md 7b: "Git is the source of truth; SQLite holds run state only and must
  be safely deletable." Plans/diffs/reviews are the work product (belongs with the project,
  inspectable on disk); volatile pipeline state (status, note) belongs in the DB.
- **Alternatives rejected:** everything in SQLite (artifacts invisible in the repo);
  everything in files (status updates would be racy file edits).

### D-013 — Workflow is a synchronous three-role pipeline behind an adapter interface
- **Decision:** `Workflow.run()` drives submitted -> planning -> executing -> reviewing ->
  awaiting_approval synchronously; roles speak one interface (`plan`, `execute`, `review`)
  and never see each other's reasoning (review reads plan + diff + principles only).
- **Reason:** AGENTS.md section 1 and 7b role isolation; mocks first (work order step 5);
  synchronous is sufficient for milestone M1 (mock adapters finish instantly) and avoids
  background-job complexity before it earns its keep.
- **Alternatives rejected:** a queue/worker setup (no need at this scale, adds failure modes);
  roles reading each other's chain-of-thought (forbidden).

### D-014 — Failed review loops back, max 3, then stops for the human
- **Decision:** If review reports failed rules: back to executing (loop_count += 1). After
  3 loops, status becomes `failed` and the engine stops; UI shows the failed rules first
  plus guidance. A human note (`Request changes`) also returns the request to executing.
- **Reason:** Exact lifecycle from AGENTS.md 7b: "(fail: back to executing, max 3 loops, then
  stop and ask human)".
- **Alternatives rejected:** auto-run forever; silently dropping the request.

### D-015 — Startup fails fast on invalid or indistinct models.yaml
- **Decision:** `create_app()` loads `CALIPER_MODELS_CONFIG` (default `caliper/models.yaml`)
  and `load_models_config` raises unless: exactly the roles planning/execution/review exist,
  each has model+adapter (`mock` or `http`), `http` roles have base_url, and all three model
  ids are distinct. Any violation aborts app creation.
- **Reason:** AGENTS.md section 3: "startup validation fails if any two roles resolve to the
  same model." Failing at import makes a misconfiguration impossible to miss.
- **Alternatives rejected:** warning only (silent blunder risk); runtime validation (too late).

### D-016 — Real adapters are OpenAI-compatible HTTP; mock remains the default
- **Decision:** `caliper/http_models.py` implements the three roles over
  `{base_url}/chat/completions` (httpx). Planner must cite rule IDs (validated), executor
  returns a fenced diff (fence extracted), reviewer must return JSON verdicts
  {rule_id: {status, reason}} (validated per rule; invalid review -> WorkflowError).
  `models.yaml` ships with `adapter: mock` for all roles; switching to `http` is a config
  edit. Not yet exercised against a live model backend.
- **Reason:** Pluggable backend per AGENTS.md 3, one adapter interface, roles read files and
  produce one file each; review with no rule IDs is invalid (section 4).
- **Alternatives rejected:** SDK-specific adapters (vendor lock); auto-enabling real models
  (AI roles disabled by default).

### D-017 — Deploy is an adapter too; mock records deploys
- **Decision:** `Workflow` takes a deploy adapter (default `MockDeployAdapter`, writes
  deploy-dev/prod logs). Approve&merge writes `merged.marker`, auto-deploys Dev
  (`deployed_dev`); a second explicit human gate with a confirm dialog deploys Prod (`live`).
  The real SSH `docker compose` adapter is not implemented yet.
- **Reason:** Human approval mandatory for merge AND Prod (AGENTS.md 3/7b). The SSH/Docker
  runtime (Dev machine, AWS EC2) does not exist in this environment, so the adapter is
  deferred rather than faked; the interface is already shaped for it.
- **Alternatives rejected:** merging to real git branches now (no git layer yet; mock
  execution writes no real code); skipping the Prod gate.

### D-018 — Dashboard home + live polling without HTMX
- **Decision:** `/` is a dashboard (NEEDS YOU / IN PROGRESS / RECENT, "All clear" when empty).
  Live updates: a ~30-line vanilla-JS poller hits `/status` (JSON snapshot of statuses +
  pass/fail dot counts) every 3s and updates status pills; list page moved to `/projects`.
  HTMX is not introduced yet; no external CDNs (works offline).
- **Reason:** AGENTS.md 7: request states always visible and color-coded, live progress
  without page reload. Pending pills differ only when review progress exists (running
  request shows piling dots) — minimal, no dependency added.
- **Alternatives rejected:** HTMX partial wiring now (single dependency for one endpoint;
  can be added when partial updates pluralize); websockets (overkill for 3s polling).

### D-019 - Principles live in the project repository, and Caliper never invents a rule
- **Decision:** The principle files (`.caliper/principles/<key>.md`) in the project repository
  are the source of truth. Caliper reads them from the default branch, writes them back with a
  commit on that branch, and keeps only the state in the project manifest: Missing (no file in
  the repository), Draft (present or edited, not approved) and Approved (approved at a recorded
  content hash and commit). Editing an approved file resets it to Draft. A new principle file is
  a heading and one instruction line: there are no generated or pre-filled rules anywhere. A
  rule is a list item whose text starts with an ID of the form `XXX-NNN`; approval needs at
  least one rule and unique IDs. The workflow takes its rule IDs from the approved principles
  and refuses to run a project without them. API: `GET /api/projects/{id}/principles`,
  `GET /api/projects/{id}/principles/{key}`, `PUT /api/projects/{id}/principles/{key}`,
  `POST /api/projects/{id}/principles/sync`, `POST /api/projects/{id}/principles/{key}/approve`,
  `POST /api/projects/{id}/principles/{key}/unapprove`.
- **Reason:** AGENTS.md 12.4 and 12.8. The old implementation wrote 25 invented rules into
  `projects/<id>/principles/*.md` on this disk: Caliper judged the human's code against rules it
  had made up, and the files were not in the repository where the human works.
- **Alternatives rejected:** keeping the generated rules as a starting point (they are invented,
  not the project's); keeping the principles on this disk (the repository is the source of truth
  and the files must survive a reinstall); approving without a content hash (an approval must
  name the exact content it approves); accepting any text as a rule ID (the reviewer needs a
  stable ID per rule).

### D-020 - One SSH key per environment, the host key is pinned, failures are plain
- **Decision:** Every environment (Dev, Prod) gets its own ed25519 key under
  `CALIPER_SECRETS_DIR` with mode 600; Caliper never stores a password. The API returns the
  public key, the key path and the exact `ssh-copy-id -i <key path> <user>@<host>` command the
  human runs once, outside Caliper. The host key is scanned on the first successful connection,
  pinned in a per-environment `known_hosts` file and shown as a SHA256 fingerprint; a later
  change fails loudly and names the fix. "Test connection" uses `BatchMode` and checks
  `ssh <host> true`, `docker compose version` and that the deploy path exists and is writable;
  every failure is mapped to a plain reason with the exact fix command. The repository gets the
  same treatment: a per-project deploy key and a read/write probe (`ls-remote` plus a push
  dry-run). API: `GET /api/projects/{id}/repository`, `POST .../repository/key`,
  `POST .../repository/test`, `GET /api/projects/{id}/environments`,
  `PUT /api/projects/{id}/environments/{env}`, `POST .../environments/{env}/key`,
  `POST .../environments/{env}/test`.
- **Reason:** AGENTS.md 12.3, 12.5 and 12.8. The old environment test accepted any host key
  (`StrictHostKeyChecking=accept-new`), so a rebuilt machine was silently trusted, and the
  failure messages were raw ssh output.
- **Alternatives rejected:** storing the environment password (credentials are never stored);
  `accept-new` host keys (a changed host key must fail loudly); one key for both environments
  (a key per environment keeps the blast radius small and lets one environment be revoked
  alone); raw command output as the error (the human needs the reason and the fix).

### D-021 - Project creation asks only name + repo URL; requests are gated by setup
- **Decision:** Creating a project takes two fields only: name and an existing GitHub repository
  URL. The project page shows four setup items: Repository access, Principles (6), Dev
  environment, Prod environment. Request pages and submission stay blocked with HTTP 409 and
  a one-line reason until all four are complete. No stack preset is stored; TechStack is one of
  the six principles files.
- **Reason:** AGENTS.md 7 and 12.2. Asking for a preset duplicated the TechStack principles and
  let the UI claim readiness before keys, principles, or environments existed.
- **Alternatives rejected:** keeping repo mode/preset fields (inferable or covered elsewhere);
  allowing requests before setup (would fail later and fake progress); a separate setup wizard
  (more clicks, and the checklist already shows exactly what is missing).

### D-022 - Model roles are blank by default; mock adapters live only under `tests/`
- **Decision:** `caliper/models.yaml` starts with empty `base_url`/`model` for Planning,
  Execution and Review. The workflow refuses to run with `Models are not configured` until
  Settings > Models saves three distinct models. `caliper/` contains no mock role adapters;
  deterministic mocks live in `tests/mock_models.py` and are injected only by tests.
- **Reason:** AGENTS.md 3 and 12.1. A missing configuration must be visible and must block the
  action, never silently fall back to a mock. Three distinct models are required so Review does
  not repeat Planning's or Execution's blind spots.
- **Alternatives rejected:** keeping mock as the runtime default (fake success); generating
  placeholder model names (a misleading settings screen); one shared model for all roles
  (startup validation must reject it).

### D-023 - Safe project deletion + SSE request updates replace `/status` polling
- **Decision:** `DELETE /api/projects/{id}?stop_dev=bool&stop_prod=bool` removes Caliper's
  local copy, keys, run history and project row, but never the GitHub repository or its
  `.caliper/` files. It cancels active requests first; optional undeploys run before any
  deletion and abort everything on failure; secrets and local files are removed only under the
  resolved `CALIPER_DATA_DIR/projects/<id>` and `CALIPER_SECRETS_DIR/projects/<id>` paths;
  DB rows and an audit event are written in one transaction. The UI confirms the action and
  disables stop switches for unconfigured environments. Request pages subscribe to
  `GET /api/events` (SSE); the old `/status` poller is no longer loaded by the base template.
- **Reason:** AGENTS.md 12.7 and 7. A destructive action must be narrow, auditable, undoable
  where possible, and must not touch the repository the human owns. SSE keeps request states
  live without polling.
- **Alternatives rejected:** deleting the GitHub repository (Caliper does not own it);
  deleting `.caliper/` (principles/decisions/graph must survive); undeploying after local
  deletion (no keys left to do it); leaving active requests running (orphaned run state);
  polling every few seconds (extra requests and stale states).

### D-024 - Static assets are versioned; project settings and delete are a visible tab
- **Decision:** `style.css` and `app.js` are requested with `?v=<mtime>` so a restarted server
  cannot serve a stale bundle that lacks a new action. The project page has a `Settings` tab
  whose danger zone contains the delete button and confirmation dialog.
- **Reason:** The delete dialog existed in the DOM but an old cached `app.js` made the button
  appear inert. A destructive action must also be findable without scanning the whole page.
- **Alternatives rejected:** telling the human to hard-refresh after every deploy (easy to
  forget); renaming the bundle on every build (manual churn); putting delete only in a global
  menu (loses the project context and the request counts).

### D-025 - A finished request can be deleted with its artifacts; in-flight requests are protected
- **Decision:** `DELETE /api/requests/{id}` and `POST /requests/{id}/delete` remove one request
  row and its `plan.md`/`diff.patch`/`review.json` directory. The request page shows a
  `Danger zone` with an explicit confirmation. Requests in `planning`, `executing` or
  `reviewing` are refused with a plain reason; finished, failed, submitted and awaiting-approval
  requests can be deleted. Project deletion still removes all remaining history.
- **Reason:** Old requests otherwise accumulate forever, and the human needs a narrow action
  that is not "delete the whole project". Artifacts must disappear with the row so a later
  request id cannot inherit stale files. A running workflow must not lose its row underneath it.
- **Alternatives rejected:** deleting only the SQLite row (stale artifacts remain); allowing
  deletion while executing (race with the workflow); a bulk "clear all" button (too easy to
  destroy history by accident); reusing project deletion (removes keys and the project too).

### D-026 - Project decisions, graph and manifest live in `.caliper/` in the project repository
- **Decision:** `ProjectDocsService` reads and writes `.caliper/manifest.yaml`,
  `.caliper/graph.yaml` and `.caliper/decisions/*.md` in the project's Git repository. The
  project page shows all three and offers `Sync from repository` and `Create missing docs`.
  `Create missing docs` only writes missing files and commits them; it never overwrites an
  existing file. Decisions are append-only Markdown files. The graph and decisions are included
  in the Planning, Execution and Review model prompts. The local
  `projects/<id>/manifest.yaml` remains the runtime/secret store (deploy keys, environment
  connection state, approval hashes) because it must exist before the repository is cloned.
- **Reason:** AGENTS.md 7b makes the project repository the source of truth for project docs.
  Keeping decisions and graph only in Caliper's own `docs/` meant projects did not carry their
  own architecture or decision history, and models never saw either. A local clone cache keeps
  page loads offline; an explicit sync is the only network read.
- **Alternatives rejected:** reading the repository on every page load (slow, fails offline);
  putting project docs only in SQLite (not versioned with the code); auto-overwriting graph or
  decisions during sync (destroys human-authored content); putting secrets in the repository
  manifest (leaks credentials); requiring a new setup-checklist item before requests (docs can
  be initialized after the first sync).

### D-027 - Decisions can be authored from the project Docs section
- **Decision:** The project Docs section has an `Add decision` form and
  `POST /api/projects/{id}/decisions`. It appends one Markdown file under
  `.caliper/decisions/`, commits and pushes it to the default branch. Existing decision files are
  never edited or deleted. Title, decision and reason are required; alternatives are optional.
- **Reason:** `append_decision` existed only as an unused service method. Decisions are a core
  project entity, and a human needs a direct path to record one without leaving Caliper or
  hand-writing a filename. Keeping it append-only preserves the decision history.
- **Alternatives rejected:** editing decisions in place (destroys history); letting a model write
  decisions without human action (not human-authored); requiring a Git client (breaks the
  single-screen workflow); adding a separate Decisions page (more navigation for one small form).

### D-028 - models.yaml gains a per-role provider field; HTTP stays the default
- **Decision:** Each role may declare `provider: http | opencode` (blank means `http`, so every
  existing models.yaml still loads unchanged), an optional `timeout` in seconds (default 600) and,
  for OpenCode roles, an optional `executable` (default `opencode`). An `http` role needs base_url
  and model together (unchanged); an `opencode` role needs only a model id in `provider/model`
  form — a base URL is rejected with `does not use a base URL`. `normalize_settings`, `status`,
  `configured`, `missing_roles`, `save` and the adapter factory are provider-aware; Settings >
  Models shows a provider select, hides the base URL/API-key fields for OpenCode roles and keeps
  the Test button working for both paths (probe per provider).
- **Reason:** The OpenAI-compatible HTTP path fits a local Ollama as-is (verified: Ollama 0.40.6
  on 127.0.0.1:11434/v1), but OpenCode does not expose such an endpoint, so its roles must carry a
  different provider type instead of being crammed into base_url. Keeping `http` implicit
  preserves backwards compatibility with every shipped models.yaml.
- **Alternatives rejected:** a second `opencode_*` mixin on the same role (hidden routing rules,
  hard to validate); mandating `provider:` in every entry (breaks existing files for no gain);
  storing the OpenCode executable path in code (machines differ).

### D-029 - OpenCode runs one prompt at a time through `opencode run --format json`
- **Decision:** `caliper/opencode_models.py` adds `OpenCodeClient`, a `post()`-compatible stand-in
  for `ChatClient` that runs `opencode run --format json --model <provider/model> <prompt>`.
- **Reason:** The installed OpenCode (v2.1.2) has no OpenAI-compatible `/chat/completions`
  endpoint, but `run --format json` is a documented machine-readable mode: one JSON object per
  line, the answer in `type: "text"` parts. The prompt is one argv element (no shell anywhere),
  the timeout is bounded, `--auto` is never passed (a headless role must not be able to approve
  its own tool actions), error output passes through token redaction, and failures for a missing
  executable, nonzero exit, timeout, and empty or malformed output are plain-language errors.
  OpenCode keeps its own credentials and its own model list (`opencode models`); Caliper stores
  and hardcodes neither. Real smoke tests passed against the installed CLI: `OK` from
  `opencode/glm-5.3-flash-free`, a clear failure for a nonexistent model id, and `OK` from Ollama
  `minimax-m3.1-flash:cloud` through the plain HTTP path. A local smoke command exists:
  `python -m caliper.smoke planning|execution|review [--timeout N]`.
- **Alternatives rejected:** assuming an OpenAI-compatible OpenCode endpoint (it does not exist);
  parsing the human-oriented default output (brittle); `--auto` (unattended privilege escalation);
  routing OpenCode through the HTTP adapter (no compatible endpoint); a long-running `opencode
  serve` integration (process lifecycle management this increment does not need).

### D-030 - Role distinctness stays on the model id; the provider is a transport
- **Decision:** The three-roles-distinctness check continues to compare model ids only, across
  all providers.
- **Reason:** AGENTS.md 3 requires three different models so that no part repeats its own blind
  spots. What is duplicated is the *model*, not the transport: the same model id reached through
  HTTP and through OpenCode is still one reviewer. Comparing `(provider, model)` would have let
  the same model serve two roles through two backends and weakened the rule. Known limitation,
  accepted and documented: one concrete model exposed with different id strings by different
  transports (for example Ollama's `minimax-m3.1-flash:cloud` directly versus
  `ollama/minimax-m3.1-flash:cloud` through OpenCode) cannot be caught without a shared model
  registry that does not exist.
- **Alternatives rejected:** `(provider, model)` identity (allows the blind-spot repetition the
  rule exists to prevent); comparing normalized base names (guesswork across naming schemes).

### D-031 - Patch-safety primitives exist; runtime wiring into Execution is the next task
- **Decision:** `caliper/patch_safety.py` provides the tested primitives for real
  repository-aware execution, and nothing more for now. `validate_patch` statically rejects
  empty, malformed (no ---/+++ headers), overlarge (>2M chars) and binary patches, absolute
  paths, backslash paths, and any `..`-containing path (path traversal). `apply_patch` runs
  `git apply --check` (dry run) first, then `git apply`, via an argv array with a bounded
  timeout and the patch on stdin — only inside a directory that is a git working copy or
  worktree. `create_worktree`/`remove_worktree` isolate one request on the branch
  `caliper/<request id>` cut from the clone's HEAD (branch names are validated), never touching
  the default branch. `remote._run` gained optional `timeout` and `input_text` (backwards
  compatible).
- **Reason:** Goal B of the increment demands safe execution before touching a project
  checkout, but the full loop (execution adapter writing into a per-request worktree, running
  the project's tests there, feeding the reviewer the real diff) needs more design than one
  increment should carry unreviewed. The primitives are complete, verified against real git
  repositories, and honest about what they do not yet do: the EntryModel still returns a diff
  as text and the workflow still only stores it — no runtime behavior changed.
- **Next tracked task:** wire `create_worktree` + `apply_patch` into the workflow's executing
  step and pass test results into the review; only after that is verified may Caliper work on
  its own repository (goal C).
- **Alternatives rejected:** wiring everything now with half-verified worktree/test/merge
  behavior inside the workflow (fake completeness); relying on the model to produce safe paths
  without validation (the model cannot be trusted with the filesystem); `git apply --unsafe-paths`
  or shell execution (never).

### D-032 — A failed review re-runs Execution; request_changes calls Execution with the note
- **Decision:** When a review returns failed rule verdicts and loops remain, `Workflow`
  re-runs Execution with the plan, the failed verdicts (`failed_rules`: rule id → status/reason)
  and the human note, producing a NEW diff that is reviewed again. `request_changes` no longer
  only flips statuses: it appends the note, calls Execution with it, and reviews the new diff.
  `Execution.execute` gains an optional `feedback` argument; the HTTP adapter puts the failed
  rules and the note into its prompt and asks for a new complete diff.
- **Reason:** The previous loop re-reviewed the same diff, so a failed review could never
  improve; `request_changes` did not run anything and the human note was shown but never used.
  That made the review step decorative (PLAN_P1 section 0, item 4).
- **Alternatives rejected:** reviewing the same diff again (cannot converge, the model sees
  identical input); letting the human edit the plan by hand (kept as a manual escape hatch, but
  not the primary path); sending the full review transcript to Execution (larger prompt with no
  extra signal — only the failed verdicts and the note are sent).

### D-037 — LF line endings, `node_modules/` untracked, the esbuild bundle stays committed
- **Decision:** `.gitattributes` declares `* text=auto eol=lf`, so every text file in the
  working tree is normalized to LF on checkout and commit. `node_modules/` is removed from
  Git and ignored; `package.json` and `package-lock.json` stay committed and remain the
  reproducible definition of the build toolchain. The esbuild bundle
  `caliper/static/app.js` stays committed; it is not built at install time or at startup.
- **Reason:** The runtime is Python plus static files, and AGENTS.md section 6 says Node is
  needed only to build. Building the bundle during install or startup would add Node to the
  runtime path and could leave a half-built UI. The bundle is the artifact the current
  transitional Jinja UI serves, so keeping it in Git makes `python -m caliper` work on a
  machine without Node. `node_modules/` was about 1000 tracked files of platform-specific
  binaries that the lockfile can reproduce.
- **Known limitation:** Source (`caliper/static/js/app.js`) and bundle can drift. Until the
  React port replaces this stack, rebuild with `npm run build` whenever the source changes; CI
  does not verify the bundle because that would add a Node toolchain to the three fixed jobs.
- **Alternatives rejected:** building the bundle at install/startup (Node becomes a runtime
  dependency); serving the unbundled source (the browser cannot resolve the `motion` import);
  keeping `node_modules/` tracked (unreviewable churn, platform-specific binaries);
  committing the bundle but not the source (the bundle would become the only, opaque source).

### D-038 — Tests that need real OpenSSH tools skip with an explicit reason
- **Decision:** Real-tool tests use the `requires_ssh_keygen` and `requires_ssh_keyscan`
  markers in `tests/support.py`; a missing binary skips the test with the reason
  "ssh-keygen/ssh-keyscan is not available; this test exercises real ...". The two real-tool
  tests are `tests/test_remote_tools.py` (key generation and the plain-language failure of a
  host-key scan against an unreachable host). No existing environment, repository or deletion
  test needs a real `ssh`, `ssh-keygen` or `ssh-keyscan`: those probes inject `_run` or
  `scan_host_key` fakes.
- **Reason:** CI and developer machines may not have the OpenSSH client tools. A missing
  binary must not look like a product defect, and a blanket skip of the environment tests
  would hide tests that do not need the tools at all. Naming the tool in the skip reason
  makes the requirement visible instead of silently passing.
- **Alternatives rejected:** letting the tests fail with `FileNotFoundError` (a setup gap
  reported as a code failure); skipping the whole environment test module (they use an
  injected runner and are valuable everywhere); mocking the real-tool tests too (they would
  then prove nothing about the real binaries).

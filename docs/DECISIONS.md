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

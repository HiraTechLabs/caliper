# Caliper work log

Append-only. Add a new entry for every task and after each meaningful milestone. Never rewrite
or delete earlier entries. The canonical detailed report for each task lives under
`docs/reports/`.

## 2026-10-22 — Phase 1 audit and permanent file-based reporting

**Task:** correct the Phase 1 evidence, add the permanent reporting rule, and verify the container
acceptance criteria with disposable data.

**Branch:** `feature/phase1-container-secret-store`

### 16:36 UTC — Audit setup

- Confirmed the pre-existing `caliper_caliper-data` volume and
  `/tmp/opencode/caliper-compose/master.key` were present. Both were left untouched.
- Checked host port 8080 and found it free.
- Created an isolated Compose project (`-p p1audit`), a new disposable volume
  `p1audit_caliper-data`, and a new audit key at
  `/tmp/opencode/caliper-p1-audit-20261022/master.key` (owner `10001:10001`, mode `0400`).
- Generated the key with `.venv/bin/python -m caliper init-secrets --output ...`; no existing key
  was read, overwritten, or deleted.
- Next: build and start the audit stack, then check readiness.

### 16:37 UTC — Compose readiness

- `CALIPER_MASTER_KEY_HOST_FILE=/tmp/opencode/caliper-p1-audit-20261022/master.key docker compose -p p1audit up -d --build`
  completed; container `p1audit-caliper-1` started.
- `GET http://127.0.0.1:8080/health/ready` returned HTTP 200 with database, data directory and
  secret store all `ok`.
- Next: create a disposable project and verify its persisted state.

### 16:37 UTC — Project, principles state and in-flight request

- Created project `phase-1-audit` with `git@github.com:example/phase1-audit.git` only; no stack
  preset was supplied.
- Generated a disposable deploy key. The manifest contained all six principle state entries and
  the repository record.
- Inserted a test-only in-flight request (`req-1`, status `executing`, `base_commit=deadbeef`) into
  the disposable SQLite database to exercise shutdown and recovery. This is a database-level
  lifecycle check, not a real model run.
- Next: send SIGTERM, restart, and check the interrupted state.

### 16:38 UTC — SIGTERM and recovery

- `docker compose -p p1audit stop -t 20 caliper` returned 0; container `ExitCode=0`,
  `OOMKilled=false`.
- Container log recorded `Shutdown interrupted 1 active request(s): req-1`.
- Started the container again. `/health/ready` returned 200 and `req-1` was `interrupted` with
  `base_commit=deadbeef` preserved.
- Next: inspect the security posture and scan logs/persisted data for secrets.

### 16:40 UTC — Security inspection, persistence and secret scans

- `docker inspect` showed `User=10001:10001`, `ReadonlyRootfs=true`, `CapDrop=[ALL]`,
  `Memory=2147483648`, `SecurityOpt=[no-new-privileges:true]`.
- Mounts were only the disposable data volume at `/data` (read-write) and the master-key bind
  mount at `/run/secrets/caliper_master_key` (read-only); tmpfs `/run/caliper` and `/tmp`; no
  Docker socket.
- A second restart left the SHA-256 of `secrets/store.sqlite3` and the project manifest unchanged
  (the principle state and encrypted secrets survived); readiness returned 200.
- Searches found neither the master key nor `BEGIN OPENSSH PRIVATE KEY` in container logs or
  `/data`. Environment inspection showed only `CALIPER_MASTER_KEY_FILE`, not a key value.
- Runtime image: `95741952` bytes by `docker save`, inspect size `95722314`, `393MB` in
  `docker images` (uncompressed).
- Next: write the reporting system, canonical report and final validation.

### 16:42 UTC — Reporting system recorded

- Added the permanent file-based reporting rule to `AGENTS.md` section 13.
- Recorded D-068 and added the `work-reporting` node/edge to `graph.yaml` and `GRAPH.md`.
- Created this append-only log and the canonical report at
  `docs/reports/phase1-container-secret-store.md`; added a pointer to it from the historical
  `docs/PHASE1_REPORT.md`.
- Next: final host/lint/graph/container checks, then commit without merging.

### 16:47 UTC — Disposable audit stack removed

- Captured the final log/API/data scans, then ran `docker compose -p p1audit down -v`.
- The command removed only `p1audit-caliper-1`, volume `p1audit_caliper-data` and network
  `p1audit_default`.
- `docker volume ls` still lists the pre-existing `caliper_caliper-data`; it was not modified.
- Next: record the final host, lint, graph, test-image and container integration results.

### 16:49 UTC — Host suite, migration, lint and graph pass

- Host suite: `.venv/bin/python -m pytest -q` -> `258 passed, 2 skipped, 1 warning in 377.82s`.
- Migration target: `pytest tests/test_migration.py -q` -> `6 passed in 7.91s`.
- Ruff: `All checks passed!`.
- Graph check: `OK: 29 nodes, 72 edges, 68 decisions`.
- Still running: the `test`-target image build (full suite inside the image) and the final
  container integration test.

### 16:54 UTC — In-image suite and final container acceptance pass

- Rebuilt `caliper:phase1-test` from the reported tree. The `test` target ran the full suite:
  `258 passed, 2 skipped, 1 warning in 400.65s`; build exited 0.
- Rebuilt the runtime image `caliper:phase1`; final inspect size `95,722,188` bytes and
  `docker save` size `95,741,952` bytes.
- `CALIPER_CONTAINER_IMAGE=caliper:phase1 pytest tests/test_container_integration.py -q` ->
  `2 passed in 19.27s`.
- Final `ruff` -> `All checks passed!`; `graph_check.py` -> `OK: 29 nodes, 72 edges,
  68 decisions`.
- Committed the intended files on the feature branch after these checks. Nothing was merged to
  `main`; Phase 2 was not started.

## 2026-10-22 — Phase 2 readiness review (review only)

**Task:** decide whether Caliper can begin Phase 2 (Environments) and record the evidence.
**Branch:** `feature/phase1-container-secret-store`

### 18:09 UTC — Repository and test evidence

- Confirmed the working tree was clean, HEAD was `52c96fe`, and the branch was level with
  `origin/feature/phase1-container-secret-store` (`git rev-list --left-right --count` -> `0 0`).
- Confirmed `main` and `origin/main` were `7cb3dda`; the three Phase 1 commits are on the feature
  branch, not on `main`.
- Full host suite: `.venv/bin/python -m pytest -q` ->
  `258 passed, 2 skipped, 1 warning in 311.92s (0:05:11)`.
- Focused environments/remote/container run:
  `pytest -q -rs tests/test_container_integration.py tests/test_remote_tools.py tests/test_environments.py`
  -> `16 passed, 2 skipped in 12.41s`; both skips are the opt-in container acceptance tests.
- `ruff check caliper tests scripts` -> `All checks passed!`.
- `python scripts/graph_check.py` -> `OK: 29 nodes, 72 edges, 68 decisions`.

### 18:10 UTC — Phase 2 code inspection

- Verified `create_app` builds `Workflow` with no deploy adapter; `approve_merge` stops at
  `merged`; `deploy_dev` and `approve_prod` raise `DeploymentNotConfigured`.
- Verified no `.caliper/deploy.yaml`, deploy-strategy, RemoteExecutor, release/current, rollback,
  health-execution, streamed deploy-log, or app-env-value implementation exists.
- Verified the environment model still has only host, user, deploy path, compose file, health URL,
  verification and fingerprint; no port, auth method, credential reference, imported key,
  password bootstrap, or prepare-host flow.
- Verified host-key pinning and generated ed25519 keys exist, but environment tests use
  `FakeRunner`; no real `sshd` or real key-authenticated deploy test exists.
- Verified the Phase 0c real-Git loop test exists, but the real-HTTP test uses scripted responses,
  and no human-observed real-model milestone is recorded. `docs/AUDIT.md:78` and
  `docs/PHASE1_REPORT.md:173-174` still state that no real model run was evidenced.
- Verified `stop_environment` only supports Compose and has no port argument; stop semantics for
  the Phase 2 `image`/`dockerfile`/`script` strategies need a human decision.

### 18:11 UTC — Report and verdict

- Verdict: **NOT READY** to start Phase 2 today. G1/G3/G5 are ready; G2 (Phase 1 merge strategy)
  and G4 (manual real-model milestone) need a human decision. Phase 2 capability rows P1, P3,
  P5-P9, P10b-P13 are not implemented; P14 needs a human decision.
- Created the canonical review at `docs/reports/phase2-readiness-review.md` with the matrix,
  exact commands/results, verified-vs-assumed split, and one recommended next step.
- Recommended next step (not implemented): a read-only `.caliper/deploy.yaml` loader and Compose
  strategy detector with API tests and no deployment; gate it on the human G2/G4 decisions.
- No application code or tests were edited. No commit, merge, push, branch change, Docker volume,
  master key, credential, or user data was changed.
- No `.caliper/DECISIONS.md` or graph change was made; this review created no structural change.
- Next: stop for human approval of the verdict and the G2/G4 decisions.

## 2026-10-23 — Repository access management + knowledge workspace + graph visualization

**Task:** WS1 replace/remove a deploy key without deleting the project; WS2 unified knowledge
workspace (principles/manifest/decisions/graph views) on the project page; WS3 interactive
project graph rendering. Canonical report: `docs/reports/repository-access-and-workspace.md`.

### Implementation

- Audit first: deep-read `caliper/registry.py` (secret-store scope API), `caliper/secrets.py`,
  `caliper/remote.py`, `caliper/project_docs.py`, app structure + existing tests.
- WS1: staged deploy-key replacement in `registry` (replace -> verify -> activate -> rotated; or
  discard), credential removal that keeps the project, access-level recording in
  `test_repository_access`; API + form endpoints in `main.py`; repository section of the
  project page rewritten (`project_detail.html`).
- WS2: `project_docs.py` gained `normalize_graph`, `decision_fields`, `_redact`,
  `manifest_rows`, `manifest_workspace`, `redacted_manifest_text`; the project page gained a
  `#workspace` section with four server-side tab views (`?view=principles|manifest|decisions|graph`).
- WS3: graph payload embedded in `<script id="caliper-graph-data">`; vanilla JS renderer added to
  `caliper/static/js/app.js` (kinds layout, pan/zoom, click-to-highlight, type filters, keyboard
  support) and rebuilt with esbuild into `caliper/static/app.js`.
- One intentional rename recorded: section heading "Project docs" -> "Knowledge workspace"
  (`tests/test_project_docs.py` updated).
- Bug found by the new tests and fixed: `/projects/<id>/docs/*` form redirects with `view` produced
  a double `?` (`?view=manifest?docs_message=...`) so the view param was lost on follow;
  `_docs_redirect` now uses `&` when a view is present.
- Tests: new `tests/test_repository_access.py` (15) and `tests/test_workspace.py` (20).
- Knowledge updates: `.caliper/DECISIONS.md` D-069..D-072, `.caliper/graph.yaml` + `GRAPH.md`
  node descriptions updated (no new nodes; all changed paths already had nodes).

### Verification (actual results)

- `python scripts/graph_check.py` -> `OK: 29 nodes, 72 edges, 72 decisions`.
- `ruff check caliper tests scripts` -> `All checks passed!`.
- `.venv/bin/python -m pytest tests/test_workspace.py tests/test_project_docs.py
  tests/test_repository_access.py -q` -> `46 passed`.
- Full suite `.venv/bin/python -m pytest -q` -> `1 failed, 292 passed, 2 skipped`.
  The single failure is `tests/test_container_assets.py
  ::test_compose_publishes_loopback_only_and_writes_only_to_mounted_paths`, caused by the
  pre-existing uncommitted `compose.yaml` change in the working tree (loopback prefix removed);
  it exists before this task and was left untouched on purpose.

### Limitations

- No real-GitHub integration: replacement verification uses the same injected access-tester
  mechanism as the existing key tests; only local `file://` remotes exercised end to end.
- Revoking the old key on GitHub after rotation is a manual step surfaced in the UI
  ("Rotation pending on GitHub"); Caliper cannot do it.
- Repository access checks use `StrictHostKeyChecking=accept-new`; no per-repo pinned
  known_hosts for repository tests (environment tests still pin).
- The compose.yaml test failure and the loopback change belong to the human's uncommitted work;
  not resolved here.

### Next

- Human decision on the `compose.yaml` port change (it fails a static invariant test).
- Feature branch commit for this task; no merge; remote push only where the human asked.

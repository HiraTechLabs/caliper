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
